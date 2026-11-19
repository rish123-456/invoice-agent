"""The invoice process: PDF in -> reasoned decision out, every step visible.

Stages
  1 Ingest           file hash, exact re-upload detection
  2 Extract text     text layer, or OCR for scanned images
  3 Parse fields     deterministic parser (+ optional LLM fallback)
  4 Validate         required fields, arithmetic, GST rate, date sanity
  5 Vendor check     vendor master by GSTIN, else by name; blocked/unknown
  6 Duplicate check  normalised invoice no., and same amount close in time
  7 PO match         explicit PO, or infer from vendor's open POs
  8 Price & balance  line prices vs PO (tolerance), remaining PO balance
  9 Decision         APPROVED / NEEDS_REVIEW / REJECTED + reasons + next actions
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

import store
from extract import REQUIRED, llm_fill_missing, parse_invoice, pdf_to_lines

DATA = Path(__file__).parent / "data"

# ---- business rules (one place, easy to explain and change) -------------
PRICE_TOL_PCT = 0.02        # unit price may differ from PO by up to 2%
AMOUNT_TOL_PCT = 0.02       # invoice may exceed remaining PO balance by 2% ...
AMOUNT_TOL_ABS = 5000.0     # ... but never by more than Rs 5,000
VALID_GST = {0.0, 0.05, 0.12, 0.18, 0.28}
SOFT_DUP_DAYS = 15          # same vendor + same total within N days = possible duplicate
MAX_INVOICE_AGE_DAYS = 180
MATCH_MIN = 0.55            # description similarity to accept a line / PO match


def load_master():
    vendors = pd.read_csv(DATA / "vendors.csv")
    pos = pd.read_csv(DATA / "purchase_orders.csv")
    lines = pd.read_csv(DATA / "po_lines.csv")
    return vendors, pos, lines


def sim(a: str, b: str) -> float:
    a, b = a.lower(), b.lower()
    ta, tb = set(re.findall(r"[a-z0-9]+", a)), set(re.findall(r"[a-z0-9]+", b))
    jac = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(SequenceMatcher(None, a, b).ratio(), jac)


def norm_invoice_no(s: str | None) -> str | None:
    """'INV/1042', 'inv-1042', 'INV 01042' -> 'INV1042'"""
    if not s:
        return None
    s = re.sub(r"[^A-Z0-9]", "", s.upper())
    return re.sub(r"(?<=[A-Z])0+(?=\d)", "", s)


def inr(x) -> str:
    """Indian grouping: 460200 -> ₹4,60,200"""
    if x is None:
        return "—"
    n = int(round(abs(x)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + parts + [tail])
    return ("-" if x < 0 else "") + "₹" + s


class Run:
    def __init__(self):
        self.stages, self.checks, self.actions = [], [], []

    def check(self, name, result, detail, severity=None, action=None):
        # result: pass | warn | fail | info ; severity: review | reject
        self.checks.append({"check": name, "result": result, "detail": detail, "severity": severity})
        if action:
            self.actions.append(action)
        return result


def run_pipeline(pdf_bytes: bytes, file_name: str, pace: float = 0.0):
    """Generator: yields one event per stage, then a final 'result' event."""
    t0 = time.time()
    r = Run()
    vendors, pos, po_lines = load_master()
    fields: dict = {}
    ctx: dict = {"file_name": file_name}

    def stage(name, status, detail, data=None):
        ev = {"type": "stage", "stage": name, "status": status, "detail": detail, "data": data or {},
              "checks": [c for c in r.checks if c.get("_stage") is None], "at": datetime.now().isoformat(timespec="seconds")}
        for c in r.checks:
            c["_stage"] = name
        r.stages.append({k: v for k, v in ev.items() if k != "type"})
        if pace:
            time.sleep(pace)
        return ev

    def status_of(names):
        res = [c["result"] for c in r.checks if c.get("_stage") is None]
        return "fail" if "fail" in res else "warn" if "warn" in res else "done"

    # 1 Ingest ----------------------------------------------------------
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    ctx["sha"] = sha
    same_file = [x for x in store.all_runs() if (x.get("fields_json") or "").find(sha) >= 0]
    if same_file:
        r.check("Exact file re-upload", "warn",
                f"Identical file already processed in run #{same_file[0]['run_id']} ({same_file[0]['decision']})")
    yield stage("Ingest", status_of(None), f"{file_name} · {len(pdf_bytes)/1024:.0f} KB · sha256 {sha[:10]}…")

    # 2 Extract ---------------------------------------------------------
    try:
        lines, method = pdf_to_lines(pdf_bytes)
    except Exception as e:
        r.check("Readable document", "fail", f"Could not read PDF: {e}", "review",
                "Ask the vendor to re-send a readable PDF")
        yield stage("Extract text", "fail", str(e))
        yield from _finish(r, ctx, fields, t0)
        return
    if method == "ocr":
        r.check("Document type", "info", "No text layer — scanned image, read with OCR. Lower confidence on small print.")
    yield stage("Extract text", status_of(None), f"{method} · {len(lines)} lines", {"raw_text": "\n".join(lines)})

    # 3 Parse -----------------------------------------------------------
    fields = parse_invoice(lines)
    fields["sha256"] = sha
    fields["extraction_method"] = method
    fields, llm_note = llm_fill_missing(lines, fields)
    if llm_note:
        r.check("LLM fallback", "info", llm_note)
    for n in fields.get("notes", []):
        r.check("Normalisation", "info", n)
    found = [k for k in ("invoice_number", "invoice_date", "po_number", "vendor_gstin", "subtotal", "tax", "total")
             if fields.get(k) is not None]
    yield stage("Parse fields", status_of(None),
                f"{len(found)}/7 header fields, {len(fields['line_items'])} line item(s)",
                {"fields": {k: fields.get(k) for k in ("invoice_number", "invoice_date", "po_number", "vendor_gstin",
                                                        "subtotal", "tax", "total", "tax_inclusive", "gst_rate")},
                 "line_items": fields["line_items"]})

    # 4 Validate --------------------------------------------------------
    missing = [k for k in REQUIRED if not fields.get(k)]
    if missing:
        r.check("Required fields", "fail", f"Missing: {', '.join(missing)}", "review",
                f"Request a corrected invoice from the vendor showing {', '.join(missing)}")
    else:
        r.check("Required fields", "pass", "Invoice number, date and total present")
    li_sum = round(sum(li["amount"] for li in fields["line_items"]), 2)
    if fields["line_items"] and fields.get("subtotal") is not None:
        if abs(li_sum - fields["subtotal"]) > 1:
            r.check("Line items add up", "fail", f"Lines sum to {inr(li_sum)} but subtotal says {inr(fields['subtotal'])}",
                    "review", "Ask vendor to correct the arithmetic")
        else:
            r.check("Line items add up", "pass", f"Lines = subtotal = {inr(li_sum)}")
    if None not in (fields.get("subtotal"), fields.get("tax"), fields.get("total")):
        if abs(fields["subtotal"] + fields["tax"] - fields["total"]) > 1:
            r.check("Subtotal + tax = total", "fail", "Totals don't reconcile", "review")
        else:
            r.check("Subtotal + tax = total", "pass", f"{inr(fields['subtotal'])} + {inr(fields['tax'])} = {inr(fields['total'])}")
        if fields["subtotal"]:
            eff = fields["tax"] / fields["subtotal"]
            if min(abs(eff - g) for g in VALID_GST) > 0.005:
                r.check("GST rate", "warn", f"Effective tax {eff:.1%} is not a standard GST slab", "review",
                        "Confirm GST treatment with the tax team")
            else:
                r.check("GST rate", "pass", f"Effective GST {eff:.0%}")
    if fields.get("invoice_date"):
        d = date.fromisoformat(fields["invoice_date"])
        age = (date.today() - d).days
        if age < 0:
            r.check("Invoice date", "fail", f"Dated in the future ({d})", "review")
        elif age > MAX_INVOICE_AGE_DAYS:
            r.check("Invoice date", "warn", f"{age} days old — outside the {MAX_INVOICE_AGE_DAYS}-day window", "review")
        else:
            r.check("Invoice date", "pass", f"{d} ({age} days ago)")
    yield stage("Validate", status_of(None), "Completeness and arithmetic")

    # 5 Vendor ----------------------------------------------------------
    vendor = None
    g = fields.get("vendor_gstin")
    by_gstin = vendors[vendors.gstin == g] if g else vendors.iloc[0:0]
    header = fields.get("header_text", "")
    name_scores = sorted(((sim(v["name"], header[: len(v["name"]) + 25]), v) for _, v in vendors.iterrows()),
                         key=lambda x: -x[0])
    best_score, best_v = name_scores[0]
    if len(by_gstin):
        vendor = by_gstin.iloc[0]
        r.check("Vendor identified", "pass", f"{vendor['name']} ({vendor['vendor_id']}) by GSTIN {g}")
    elif best_score >= 0.8 and g and g != best_v["gstin"]:
        vendor = best_v
        r.check("Vendor GSTIN mismatch", "fail",
                f"Name looks like {best_v['name']} but GSTIN {g} ≠ master {best_v['gstin']} — possible impersonation",
                "review", "Verify vendor identity and bank details by phone before paying")
    elif best_score >= 0.8:
        vendor = best_v
        r.check("Vendor identified", "warn",
                f"GSTIN unreadable/missing; matched {best_v['name']} by name ({best_score:.0%})", None)
    else:
        r.check("Vendor identified", "fail", f"Not in vendor master (GSTIN {g or 'n/a'})", "reject",
                "Return to sender: vendor must complete onboarding before invoices can be paid")
    if vendor is not None and vendor["status"] != "active":
        r.check("Vendor status", "fail", f"Vendor is {vendor['status'].upper()} in master", "reject",
                "Do not pay. Notify procurement that a blocked vendor is invoicing")
    if vendor is not None:
        ctx.update(vendor_id=vendor["vendor_id"], vendor_name=vendor["name"])
    yield stage("Vendor check", status_of(None), ctx.get("vendor_name", "Unknown vendor"))

    if vendor is None or vendor["status"] != "active":
        yield from _finish(r, ctx, fields, t0)
        return

    # 6 Duplicates ------------------------------------------------------
    key = norm_invoice_no(fields.get("invoice_number"))
    ctx["invoice_key"] = key
    prior = store.prior_invoices(vendor["vendor_id"])
    hard = [p for p in prior if key and p["invoice_key"] == key]
    soft = []
    if fields.get("total") and fields.get("invoice_date"):
        d = date.fromisoformat(fields["invoice_date"])
        for p in prior:
            if p in hard or not p["invoice_date"] or p["total"] is None:
                continue
            if abs(p["total"] - fields["total"]) < 1 and abs((date.fromisoformat(p["invoice_date"]) - d).days) <= SOFT_DUP_DAYS:
                soft.append(p)
    if hard:
        p = hard[0]
        r.check("Duplicate invoice number", "fail",
                f"'{fields['invoice_number']}' normalises to {key} = '{p['invoice_number']}' from run #{p['run_id']} "
                f"({p['decision']}, {inr(p['total'])})", "reject",
                f"Reject as duplicate of {p['invoice_number']}; reply to vendor that it is already {p['decision'].lower().replace('_', ' ')}")
    elif soft:
        p = soft[0]
        r.check("Possible duplicate", "warn",
                f"Same vendor and same total {inr(p['total'])} as {p['invoice_number']} (run #{p['run_id']}) within {SOFT_DUP_DAYS} days",
                "review", "Check with vendor whether this is a re-bill")
    else:
        r.check("Duplicate check", "pass", f"No match among {len(prior)} prior invoice(s) from this vendor")
    yield stage("Duplicate check", status_of(None), f"key {key}")

    if hard:
        yield from _finish(r, ctx, fields, t0)
        return

    # 7 PO match --------------------------------------------------------
    po = None
    vpos = pos[(pos.vendor_id == vendor["vendor_id"]) & (pos.status == "open")]
    if fields.get("po_number"):
        hit = pos[pos.po_number == fields["po_number"]]
        if not len(hit):
            r.check("PO exists", "fail", f"{fields['po_number']} not found in PO register", "review",
                    "Ask the requester/vendor for the correct PO")
        elif hit.iloc[0]["vendor_id"] != vendor["vendor_id"]:
            r.check("PO belongs to vendor", "fail",
                    f"{fields['po_number']} was raised on {hit.iloc[0]['vendor_id']}, not {vendor['vendor_id']}", "review")
        else:
            po = hit.iloc[0]
            r.check("PO match", "pass", f"{po['po_number']} — {po['description']} (explicit reference)")
    if po is None and not fields.get("po_number"):
        cands = []
        for _, p in vpos.iterrows():
            pl = po_lines[po_lines.po_number == p["po_number"]]
            ds = max((sim(li["description"], x["description"]) for li in fields["line_items"] for _, x in pl.iterrows()),
                     default=0)
            remaining = p["subtotal"] - store.billed_to_date(p["po_number"])
            amt = 1 - min(1, abs((fields.get("subtotal") or 0) - remaining) / max(remaining, 1))
            cands.append((0.7 * ds + 0.3 * amt, p, ds, amt))
        cands.sort(key=lambda x: -x[0])
        if cands and cands[0][0] >= MATCH_MIN:
            score, po, ds, amt = cands[0]
            r.check("PO inferred", "warn",
                    f"No PO on invoice. Best match {po['po_number']} ({po['description']}) — confidence {score:.0%} "
                    f"(description {ds:.0%}, amount {amt:.0%}) from {len(cands)} open PO(s)", "review",
                    f"Buyer to confirm {po['po_number']} is the right PO (one click)")
        else:
            r.check("PO match", "fail", f"No PO on invoice and none of {len(vpos)} open PO(s) fit", "review",
                    "Route to requester: non-PO spend needs approval")
    yield stage("PO match", status_of(None), po["po_number"] if po is not None else "no PO")

    # 8 Price & balance -------------------------------------------------
    comparison = []
    if po is not None:
        ctx["po_number"] = po["po_number"]
        pl = po_lines[po_lines.po_number == po["po_number"]]
        for li in fields["line_items"]:
            best = max(((sim(li["description"], x["description"]), x) for _, x in pl.iterrows()), key=lambda t: t[0])
            s, x = best
            row = {"invoice_line": li["description"], "po_line": x["description"] if s >= MATCH_MIN else None,
                   "inv_qty": li["qty"], "po_qty": x["qty"], "inv_rate": li["unit_price"], "po_rate": x["unit_price"]}
            if s < MATCH_MIN:
                row["result"] = "no matching PO line"
                r.check("Line match", "fail", f"'{li['description']}' isn't on {po['po_number']}", "review",
                        "Ask requester whether this item was ordered")
            elif po["allows_partial_billing"] == "yes" and x["qty"] == 1:
                row["result"] = "milestone / value line — checked on PO balance"
            else:
                var = (li["unit_price"] - x["unit_price"]) / x["unit_price"]
                row["variance"] = f"{var:+.1%}"
                if abs(var) <= PRICE_TOL_PCT:
                    row["result"] = "within tolerance" if var else "exact"
                    r.check("Unit price", "pass" if not var else "info",
                            f"{li['description']}: {inr(li['unit_price'])} vs PO {inr(x['unit_price'])} ({var:+.1%}, tolerance ±{PRICE_TOL_PCT:.0%})")
                else:
                    row["result"] = "OUT of tolerance"
                    extra = (li["unit_price"] - x["unit_price"]) * li["qty"]
                    r.check("Unit price", "fail",
                            f"{li['description']}: {inr(li['unit_price'])} vs PO {inr(x['unit_price'])} ({var:+.1%}) — "
                            f"{inr(extra)} over PO on {li['qty']:g} units", "review",
                            f"Ask vendor for a credit note of {inr(extra)} + GST, or buyer to amend the PO")
                if li["qty"] > x["qty"]:
                    r.check("Quantity", "fail", f"Invoiced {li['qty']:g} > ordered {x['qty']:g}", "review")
            comparison.append(row)

        billed = store.billed_to_date(po["po_number"])
        remaining = po["subtotal"] - billed
        sub = fields.get("subtotal") or 0
        over = sub - remaining
        tol = min(AMOUNT_TOL_PCT * po["subtotal"], AMOUNT_TOL_ABS)
        bal = f"PO {inr(po['subtotal'])} · already billed {inr(billed)} · remaining {inr(remaining)} · this invoice {inr(sub)}"
        if over > tol:
            r.check("PO balance", "fail", f"Over-bills remaining balance by {inr(over)} (tolerance {inr(tol)}). {bal}",
                    "review", f"Hold. Approve only {inr(remaining)} or get a PO amendment for {inr(over)}")
        elif over > 0:
            r.check("PO balance", "info", f"Exceeds balance by {inr(over)}, within tolerance {inr(tol)}. {bal}")
        else:
            if po["allows_partial_billing"] == "no" and remaining - sub > tol:
                r.check("PO balance", "warn", f"Partial invoice on a PO that expects one bill. {bal}", "review",
                        "Confirm partial delivery with the requester")
            else:
                r.check("PO balance", "pass", bal)
        ctx["po_balance"] = {"po_total": po["subtotal"], "billed_before": billed, "remaining_before": remaining,
                             "this_invoice": sub, "remaining_after": remaining - sub}
    yield stage("Price & balance", status_of(None), f"{len(comparison)} line(s) compared",
                {"comparison": comparison, "po_balance": ctx.get("po_balance")})

    yield from _finish(r, ctx, fields, t0)


def _finish(r: Run, ctx: dict, fields: dict, t0: float):
    rejects = [c for c in r.checks if c["severity"] == "reject"]
    reviews = [c for c in r.checks if c["severity"] == "review" and c["result"] in ("fail", "warn")]
    if rejects:
        decision, reason = "REJECTED", rejects[0]["detail"]
    elif reviews:
        decision, reason = "NEEDS_REVIEW", "; ".join(c["check"] for c in reviews)
    else:
        decision = "APPROVED"
        reason = "All checks passed — scheduled for payment"
        if ctx.get("vendor_id"):
            v = load_master()[0].set_index("vendor_id").loc[ctx["vendor_id"]]
            r.actions.append(f"Schedule payment of {inr(fields.get('total'))} on net-{v['payment_terms_days']} terms")
    r.stages.append({"stage": "Decision", "status": {"APPROVED": "done", "NEEDS_REVIEW": "warn", "REJECTED": "fail"}[decision],
                     "detail": f"{decision}: {reason}", "data": {}, "checks": [],
                     "at": datetime.now().isoformat(timespec="seconds")})
    checks = [{k: v for k, v in c.items() if k != "_stage"} | {"stage": c.get("_stage") or "Decision"} for c in r.checks]
    for s in r.stages:  # strip helper keys from stored checks
        s["checks"] = [{k: v for k, v in c.items() if k != "_stage"} for c in s.get("checks", [])]
    clean_fields = {k: v for k, v in fields.items() if k != "header_text"}
    rec = {
        "started_at": datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
        "file_name": ctx["file_name"], "vendor_id": ctx.get("vendor_id"), "vendor_name": ctx.get("vendor_name"),
        "invoice_number": fields.get("invoice_number"), "invoice_key": ctx.get("invoice_key"),
        "invoice_date": fields.get("invoice_date"), "po_number": ctx.get("po_number") or fields.get("po_number"),
        "subtotal": fields.get("subtotal"), "total": fields.get("total"),
        "decision": decision, "reason": reason, "duration_ms": int((time.time() - t0) * 1000),
        "stages": r.stages, "fields": clean_fields | {"checks": checks, "po_balance": ctx.get("po_balance")},
        "actions": r.actions,
    }
    rec["run_id"] = store.save_run(rec)
    yield {"type": "stage", **r.stages[-1]}
    yield {"type": "result", **rec}


def run_sync(path: str | Path, pace: float = 0.0) -> dict:
    p = Path(path)
    result = None
    for ev in run_pipeline(p.read_bytes(), p.name, pace):
        if ev["type"] == "result":
            result = ev
    return result
