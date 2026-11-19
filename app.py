"""Streamlit UI: live run view, dashboard, review queue, master data.

Run locally:  streamlit run app.py
"""
import json
from pathlib import Path

import pandas as pd
import pymupdf
import streamlit as st

import pipeline
import store
from pipeline import inr

ROOT = Path(__file__).parent
SAMPLES = ROOT / "samples"
STAGES = ["Ingest", "Extract text", "Parse fields", "Validate", "Vendor check",
          "Duplicate check", "PO match", "Price & balance", "Decision"]
ICON = {"pending": "⚪", "running": "🔄", "done": "✅", "warn": "🟡", "fail": "🔴", "skipped": "⏭️"}
DEC = {"APPROVED": ("#0f7b3f", "#e7f6ec", "✅ Approved"),
       "NEEDS_REVIEW": ("#9a6700", "#fff6dd", "🟡 Needs review"),
       "REJECTED": ("#b42318", "#fdecea", "⛔ Rejected")}
RES_ICON = {"pass": "✅", "info": "ℹ️", "warn": "🟡", "fail": "🔴"}

st.set_page_config(page_title="Invoice Agent — PDF to decision", page_icon="🧾", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.6rem;}
.stage {border:1px solid #e5e7eb;border-radius:10px;padding:8px 12px;margin-bottom:6px;background:#fff;}
.stage.done{border-left:5px solid #16a34a}.stage.warn{border-left:5px solid #d97706}
.stage.fail{border-left:5px solid #dc2626}.stage.pending{border-left:5px solid #d1d5db;color:#9ca3af}
.stage.running{border-left:5px solid #2563eb;background:#eff6ff}.stage.skipped{border-left:5px solid #d1d5db;color:#9ca3af}
.stage b{font-size:0.95rem}.stage small{color:#4b5563}
.decision{border-radius:12px;padding:16px 20px;margin:4px 0 12px 0}
.decision h2{margin:0 0 4px 0;font-size:1.5rem}
</style>""", unsafe_allow_html=True)


def stage_html(name, status, detail=""):
    return (f"<div class='stage {status}'><b>{ICON[status]} {name}</b>"
            f"{'<br><small>' + detail + '</small>' if detail else ''}</div>")


def decision_card(rec):
    fg, bg, label = DEC[rec["decision"]]
    st.markdown(
        f"<div class='decision' style='background:{bg};border:1px solid {fg}33'>"
        f"<h2 style='color:{fg}'>{label}</h2>"
        f"<div><b>{rec.get('vendor_name') or 'Unknown vendor'}</b> · invoice {rec.get('invoice_number') or '—'}"
        f" · {rec.get('po_number') or 'no PO'} · total <b>{inr(rec.get('total'))}</b></div>"
        f"<div style='margin-top:6px'>{rec['reason']}</div></div>", unsafe_allow_html=True)


def render_details(rec, pdf_bytes=None):
    fields = rec["fields"] if isinstance(rec["fields"], dict) else json.loads(rec["fields"])
    actions = rec["actions"] if isinstance(rec["actions"], list) else json.loads(rec["actions"])
    stages = rec["stages"] if isinstance(rec["stages"], list) else json.loads(rec["stages"])
    if actions:
        st.markdown("**Next actions**")
        for a in actions:
            st.markdown(f"- {a}")
    tabs = st.tabs(["Why — every check", "Extracted data", "PO comparison", "Timeline", "Raw text", "Document"])
    with tabs[0]:
        order = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
        for c in sorted(fields.get("checks", []), key=lambda c: order[c["result"]]):
            sev = f" · *{c['severity']}*" if c.get("severity") and c["result"] in ("fail", "warn") else ""
            st.markdown(f"{RES_ICON[c['result']]} **{c['check']}** <span style='color:#6b7280'>({c['stage']}{sev})</span><br>{c['detail']}",
                        unsafe_allow_html=True)
    with tabs[1]:
        c1, c2 = st.columns([1, 2])
        hdr = {k: fields.get(k) for k in ("invoice_number", "invoice_date", "po_number", "vendor_gstin",
                                          "extraction_method", "tax_inclusive", "gst_rate", "subtotal", "tax", "total")}
        c1.dataframe(pd.DataFrame(hdr.items(), columns=["field", "value"]).astype(str), hide_index=True, width="stretch")
        if fields.get("line_items"):
            c2.dataframe(pd.DataFrame(fields["line_items"]), hide_index=True, width="stretch")
    with tabs[2]:
        comp = next((s["data"].get("comparison") for s in stages if s["stage"] == "Price & balance"), None)
        bal = fields.get("po_balance")
        if comp:
            st.dataframe(pd.DataFrame(comp), hide_index=True, width="stretch")
        if bal:
            b = st.columns(4)
            b[0].metric("PO value", inr(bal["po_total"]))
            b[1].metric("Billed before", inr(bal["billed_before"]))
            b[2].metric("This invoice", inr(bal["this_invoice"]))
            b[3].metric("Remaining after", inr(bal["remaining_after"]),
                        delta="over-billed" if bal["remaining_after"] < 0 else None, delta_color="inverse")
        if not comp and not bal:
            st.caption("Process stopped before PO matching.")
    with tabs[3]:
        st.dataframe(pd.DataFrame([{"at": s["at"], "stage": s["stage"], "status": ICON.get(s["status"], "") + " " + s["status"],
                                    "detail": s["detail"]} for s in stages]), hide_index=True, width="stretch")
    with tabs[4]:
        raw = next((s["data"].get("raw_text") for s in stages if s["stage"] == "Extract text"), "")
        st.code(raw or "(none)", language=None)
    with tabs[5]:
        src = pdf_bytes or ((SAMPLES / rec["file_name"]).read_bytes() if (SAMPLES / rec["file_name"]).exists() else None)
        if src:
            pix = pymupdf.open(stream=src, filetype="pdf")[0].get_pixmap(dpi=110)
            st.image(pix.tobytes("png"), width=620)
        else:
            st.caption("Original file not stored for uploads.")


# ------------------------------------------------------------------ pages
def page_process():
    st.title("🧾 Invoice → decision")
    st.caption("Drop in a vendor invoice PDF. The agent reads it, checks it against the vendor master and PO register, "
               "and decides — showing every step.")
    left, right = st.columns([1, 2], gap="large")
    with left:
        src = st.radio("Input", ["Demo invoice", "Upload PDF"], horizontal=True)
        if src == "Demo invoice":
            files = sorted(p.name for p in SAMPLES.glob("*.pdf"))
            name = st.selectbox("Invoice", files, format_func=lambda n: n.replace(".pdf", "").replace("_", " "))
            data = (SAMPLES / name).read_bytes()
        else:
            up = st.file_uploader("Invoice PDF", type=["pdf"])
            name, data = (up.name, up.getvalue()) if up else (None, None)
        pace = st.toggle("Slow-motion run view", value=True, help="Adds a short pause between stages so you can follow along")
        go = st.button("▶ Run process", type="primary", width="stretch", disabled=data is None)
        if data:
            pix = pymupdf.open(stream=data, filetype="pdf")[0].get_pixmap(dpi=60)
            st.image(pix.tobytes("png"), caption=name, width="stretch")
    with right:
        st.subheader("Live run")
        slots = {s: st.empty() for s in STAGES}
        for s in STAGES:
            slots[s].markdown(stage_html(s, "pending"), unsafe_allow_html=True)
        out = st.container()
        if go:
            st.session_state.pop("last", None)
            seen = []
            slots[STAGES[0]].markdown(stage_html(STAGES[0], "running", "working…"), unsafe_allow_html=True)
            result = None
            for ev in pipeline.run_pipeline(data, name, pace=0.45 if pace else 0):
                if ev["type"] == "result":
                    result = ev
                    break
                s = ev["stage"]
                seen.append(s)
                detail = ev["detail"] + "".join(
                    f"<br>{RES_ICON[c['result']]} {c['check']}: {c['detail']}" for c in ev.get("checks", [])
                    if c["result"] != "pass")
                slots[s].markdown(stage_html(s, ev["status"], detail), unsafe_allow_html=True)
                nxt = STAGES.index(s) + 1
                if s != "Decision" and nxt < len(STAGES) - 1:
                    slots[STAGES[nxt]].markdown(stage_html(STAGES[nxt], "running", "working…"), unsafe_allow_html=True)
            for s in STAGES:
                if s not in seen:
                    slots[s].markdown(stage_html(s, "skipped", "not needed — decision already reached"), unsafe_allow_html=True)
            st.session_state["last"] = (result, data)
        if "last" in st.session_state:
            result, d = st.session_state["last"]
            if not go:  # re-draw stage list after a rerun
                done = {s["stage"]: s for s in result["stages"]}
                for s in STAGES:
                    if s in done:
                        slots[s].markdown(stage_html(s, done[s]["status"], done[s]["detail"]), unsafe_allow_html=True)
                    else:
                        slots[s].markdown(stage_html(s, "skipped", "not needed"), unsafe_allow_html=True)
            with out:
                decision_card(result)
                st.caption(f"Run #{result['run_id']} · {result['duration_ms']/1000:.1f}s")
                render_details(result, d)


def page_dashboard():
    st.title("📊 Dashboard")
    runs = store.all_runs()
    if not runs:
        st.info("No runs yet — process an invoice first.")
        return
    df = pd.DataFrame(runs)
    m = st.columns(6)
    n = len(df)
    m[0].metric("Invoices processed", n)
    m[1].metric("Approved", int((df.decision == "APPROVED").sum()))
    m[2].metric("Needs review", int((df.decision == "NEEDS_REVIEW").sum()))
    m[3].metric("Rejected", int((df.decision == "REJECTED").sum()))
    m[4].metric("Value processed", f"₹{df.total.fillna(0).sum()/1e5:.1f} L", help=inr(df.total.fillna(0).sum()))
    m[5].metric("Avg time / invoice", f"{df.duration_ms.mean()/1000:.1f}s")
    blocked = df[df.decision == "REJECTED"].total.fillna(0).sum()
    held = df[df.decision == "NEEDS_REVIEW"].total.fillna(0).sum()
    st.caption(f"Payments stopped (rejected): **{inr(blocked)}** · held for review: **{inr(held)}**")

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("**Decisions**")
        st.bar_chart(df.decision.value_counts(), color="#2563eb", height=220, horizontal=True)
    with c2:
        st.markdown("**Why invoices didn't auto-approve**")
        reasons = []
        for _, r in df[df.decision != "APPROVED"].iterrows():
            for c in json.loads(r.fields_json).get("checks", []):
                if c["result"] in ("fail", "warn") and c.get("severity"):
                    reasons.append(c["check"])
        if reasons:
            st.bar_chart(pd.Series(reasons).value_counts(), color="#d97706", height=220, horizontal=True)

    st.markdown("**Run history**")
    view = df[["run_id", "started_at", "file_name", "vendor_name", "invoice_number", "po_number", "total", "decision", "reason"]].copy()
    view["total"] = view.total.map(inr)
    view["decision"] = view.decision.map(lambda d: DEC[d][2])
    st.dataframe(view, hide_index=True, width="stretch")

    st.markdown("**PO utilisation** (approved invoices only)")
    pos = pd.read_csv(pipeline.DATA / "purchase_orders.csv")
    pos["billed"] = pos.po_number.map(store.billed_to_date)
    pos["remaining"] = pos.subtotal - pos.billed
    pos["used"] = (pos.billed / pos.subtotal)
    st.dataframe(pos[["po_number", "vendor_id", "description", "subtotal", "billed", "remaining", "used"]],
                 hide_index=True, width="stretch",
                 column_config={"used": st.column_config.ProgressColumn("used", format="percent", min_value=0, max_value=1)})

    st.divider()
    pick = st.selectbox("Open a run", df.run_id, format_func=lambda i: f"#{i} · {df.set_index('run_id').loc[i, 'file_name']}")
    rec = df.set_index("run_id").loc[pick].to_dict() | {"run_id": pick}
    rec |= {"fields": rec["fields_json"], "stages": rec["stages_json"], "actions": rec["actions_json"]}
    decision_card(rec)
    render_details(rec)


def page_review():
    st.title("🗂 Review queue")
    st.caption("Exceptions the agent couldn't safely decide. A person approves or rejects; the override is logged in the audit trail.")
    q = [r for r in store.all_runs() if r["decision"] == "NEEDS_REVIEW"]
    if not q:
        st.success("Queue is empty.")
        return
    for r in q:
        with st.expander(f"#{r['run_id']} · {r['vendor_name']} · {r['invoice_number']} · {inr(r['total'])} — {r['reason']}", expanded=True):
            for c in json.loads(r["fields_json"]).get("checks", []):
                if c["result"] in ("fail", "warn"):
                    st.markdown(f"{RES_ICON[c['result']]} **{c['check']}** — {c['detail']}")
            for a in json.loads(r["actions_json"]):
                st.markdown(f"➡️ {a}")
            note = st.text_input("Reviewer note", key=f"n{r['run_id']}")
            b1, b2, _ = st.columns([1, 1, 4])
            if b1.button("Approve", key=f"a{r['run_id']}", type="primary"):
                store.set_decision(r["run_id"], "APPROVED", note)
                st.rerun()
            if b2.button("Reject", key=f"r{r['run_id']}"):
                store.set_decision(r["run_id"], "REJECTED", note)
                st.rerun()


def page_master():
    st.title("📚 Master data & rules")
    st.markdown("**Rules the agent applies**")
    st.markdown(f"""
- Unit price may differ from the PO by **±{pipeline.PRICE_TOL_PCT:.0%}**.
- An invoice may exceed the remaining PO balance by **{pipeline.AMOUNT_TOL_PCT:.0%} of PO value, capped at {inr(pipeline.AMOUNT_TOL_ABS)}**.
- Only **approved** invoices consume PO balance (so milestone / split billing is tracked correctly).
- **Duplicate** = same vendor + same invoice number after normalising (`INV/1042` = `INV-1042`) → reject.
  Same vendor + same total within **{pipeline.SOFT_DUP_DAYS} days** → review.
- Vendor identified by **GSTIN**; if unreadable, by name. Name matches but GSTIN differs → hold (impersonation risk).
- Unknown or blocked vendor → reject. No PO → infer from the vendor's open POs, but a person confirms.
- GST must be a standard slab (0/5/12/18/28%). GST-inclusive prices are converted to pre-tax before comparing.
""")
    for f, t in [("vendors.csv", "Vendor master"), ("purchase_orders.csv", "PO register"), ("po_lines.csv", "PO lines")]:
        st.markdown(f"**{t}**")
        st.dataframe(pd.read_csv(pipeline.DATA / f), hide_index=True, width="stretch")


with st.sidebar:
    st.markdown("### 🧾 Invoice Agent")
    page = st.radio("Go to", ["Process invoice", "Dashboard", "Review queue", "Master data & rules"], label_visibility="collapsed")
    st.divider()
    nq = sum(1 for r in store.all_runs() if r["decision"] == "NEEDS_REVIEW")
    st.caption(f"{nq} invoice(s) waiting for review")
    if st.button("Reset demo ledger", help="Clears run history so the demo can be replayed from scratch"):
        store.reset()
        st.session_state.pop("last", None)
        st.rerun()

{"Process invoice": page_process, "Dashboard": page_dashboard,
 "Review queue": page_review, "Master data & rules": page_master}[page]()
