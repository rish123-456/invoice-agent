"""Stage 1-2: get text out of the PDF (text layer or OCR) and parse fields.

Design choice: a deterministic parser runs first because it is fast, free,
explainable and gives the same answer every time in a live demo. An LLM is
used only as a fallback when required fields are still missing (set
ANTHROPIC_API_KEY to enable it). Either way the result goes through the same
validation, so an LLM mistake can't slip straight into a payment decision.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime

import pymupdf

try:
    import pytesseract
    from PIL import Image
except ImportError:  # OCR optional
    pytesseract = None

AMT = r"\d{1,3}(?:,\d{2,3})*\.\d{2}|\d+\.\d{2}"
BUYER_GSTIN = "07AAACA0001A1Z9"


def to_num(s: str) -> float:
    return float(s.replace(",", ""))


# ------------------------------------------------------------------ text
def pdf_to_lines(pdf_bytes: bytes) -> tuple[list[str], str]:
    """Return (lines, method). Rebuilds visual lines from word positions so
    table rows stay on one line; falls back to OCR for image-only pages."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    lines: list[str] = []
    for page in doc:
        words = page.get_text("words")  # x0,y0,x1,y1,word,block,line,wno
        rows: dict[int, list] = {}
        for w in words:
            key = round(w[3] / 4)  # bucket by baseline
            rows.setdefault(key, []).append(w)
        for key in sorted(rows):
            lines.append(" ".join(w[4] for w in sorted(rows[key], key=lambda w: w[0])))
    if sum(len(l) for l in lines) > 80:
        return lines, "text-layer"
    if pytesseract is None:
        raise RuntimeError("Scanned PDF and OCR (pytesseract/tesseract) is not installed")
    lines = []
    for page in doc:
        pix = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        txt = pytesseract.image_to_string(img, config="--psm 6")
        lines += [l.strip() for l in txt.splitlines() if l.strip()]
    return lines, "ocr"


# ------------------------------------------------------------------ parse
def _parse_date(s: str) -> str | None:
    s = s.strip().replace(" ", "-")
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _fix_gstin(g: str) -> str:
    # OCR confuses O/0 in the numeric positions of a GSTIN
    g = list(g)
    for i in (0, 1, 7, 8, 9, 10):
        if g[i] == "O":
            g[i] = "0"
    return "".join(g)


TOTAL_LABELS = {
    "subtotal": r"sub\s*-?\s*total|taxable\s+value",
    "tax": r"\b[csi]?gst\b\s*@|\bigst\b|\bcgst\b|\bsgst\b",
    "total": r"grand\s+total|amount\s+due|total\s+payable|invoice\s+total|total\s+amount",
}


def parse_invoice(lines: list[str]) -> dict:
    text = "\n".join(lines)
    f: dict = {"line_items": [], "notes": []}

    m = re.search(r"(?:invoice\s*(?:no\.?|number|#)|bill\s*no\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9/\-]*\d[A-Z0-9/\-]*)",
                  text, re.I)
    f["invoice_number"] = m.group(1) if m else None

    m = re.search(r"(?:invoice\s*date|dated|date)\s*:?\s*(\d{1,2}[-/ .][A-Za-z]{3,9}[-/ .]\d{4}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{2}-\d{2})",
                  text, re.I)
    f["invoice_date"] = _parse_date(m.group(1)) if m else None

    m = re.search(r"\bP\.?O\.?[-\s]?(\d{4,6})\b", text, re.I)
    f["po_number"] = f"PO-{m.group(1)}" if m else None

    gstins = [_fix_gstin(g) for g in re.findall(r"\b[0-9O]{2}[A-Z]{5}[0-9O]{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", text)]
    sellers = [g for g in gstins if g != BUYER_GSTIN]
    f["vendor_gstin"] = sellers[0] if sellers else None
    f["header_text"] = " ".join(lines[:4])  # vendor name usually lives here

    incl = re.search(r"inclusive\s+of\s+(?:gst|tax)[^\d]*(\d+(?:\.\d+)?)?\s*%?", text, re.I)
    f["tax_inclusive"] = bool(incl)
    rate = re.search(r"\b(?:gst|igst)\s*@\s*(\d+(?:\.\d+)?)\s*%", text, re.I)
    if incl and incl.group(1):
        f["gst_rate"] = float(incl.group(1)) / 100
    elif rate:
        f["gst_rate"] = float(rate.group(1)) / 100
    else:
        cg = re.search(r"cgst\s*@\s*(\d+(?:\.\d+)?)", text, re.I)
        f["gst_rate"] = 2 * float(cg.group(1)) / 100 if cg else None

    f["subtotal"] = f["tax"] = f["total"] = None
    tax_sum = 0.0
    item_re = re.compile(rf"^(?P<desc>[A-Za-z].*?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<rate>{AMT})\s+(?P<amt>{AMT})$")
    for line in lines:
        amts = re.findall(AMT, line)
        low = line.lower()
        if amts and re.search(TOTAL_LABELS["subtotal"], low):
            f["subtotal"] = to_num(amts[-1])
        elif amts and re.search(TOTAL_LABELS["tax"], low) and "inclusive" not in low:
            tax_sum += to_num(amts[-1])
        elif amts and re.search(TOTAL_LABELS["total"], low):
            f["total"] = to_num(amts[-1])
        else:
            mi = item_re.match(line.strip())
            if mi:
                f["line_items"].append({
                    "description": mi["desc"].strip(),
                    "qty": float(mi["qty"]),
                    "unit_price": to_num(mi["rate"]),
                    "amount": to_num(mi["amt"]),
                })
    f["tax"] = round(tax_sum, 2) if tax_sum else None

    # GST-inclusive invoices: back out the tax so we compare like-for-like with the PO
    if f["tax_inclusive"] and f["gst_rate"]:
        r = 1 + f["gst_rate"]
        gross = f["total"] or sum(li["amount"] for li in f["line_items"])
        f["subtotal"] = round(gross / r, 2)
        f["tax"] = round(gross - f["subtotal"], 2)
        f["total"] = gross
        for li in f["line_items"]:
            li["unit_price_incl_tax"] = li["unit_price"]
            li["unit_price"] = round(li["unit_price"] / r, 2)
            li["amount"] = round(li["amount"] / r, 2)
        f["notes"].append(f"Prices were GST-inclusive @ {f['gst_rate']*100:g}% — converted to pre-tax for PO comparison")
    elif f["subtotal"] is None and f["line_items"]:
        f["subtotal"] = round(sum(li["amount"] for li in f["line_items"]), 2)
        f["notes"].append("Subtotal not printed — computed from line items")
    if f["total"] is None and f["subtotal"] is not None and f["tax"] is not None:
        f["total"] = round(f["subtotal"] + f["tax"], 2)
        f["notes"].append("Total not printed — computed as subtotal + tax")
    return f


REQUIRED = ["invoice_number", "invoice_date", "total"]


def llm_fill_missing(lines: list[str], fields: dict) -> tuple[dict, str | None]:
    """Optional: ask Claude to fill only the fields the parser missed."""
    missing = [k for k in REQUIRED + ["po_number"] if not fields.get(k)]
    key = os.getenv("ANTHROPIC_API_KEY")
    if not missing or not key:
        return fields, None
    import requests
    prompt = (
        "Extract these fields from the invoice text below and reply with ONLY a JSON object. "
        f"Fields: {missing}. Use null if a field is truly absent - never guess. "
        "Dates as YYYY-MM-DD, amounts as numbers.\n\n" + "\n".join(lines)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": os.getenv("LLM_MODEL", "claude-haiku-4-5"), "max_tokens": 400,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        filled = [k for k in missing if data.get(k)]
        for k in filled:
            fields[k] = data[k]
        return fields, f"LLM filled: {filled}" if filled else "LLM found nothing extra"
    except Exception as e:  # never let the fallback break the run
        return fields, f"LLM fallback failed ({type(e).__name__}) — continuing with parsed fields"
