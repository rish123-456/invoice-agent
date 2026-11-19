"""Generate realistic test invoices (PDF) for the demo.

Three different vendor layouts, one scanned (image-only) invoice, and GST
either separated or embedded in prices -- so the extractor has to cope with
real-world variety instead of one fixed template.

Run:  python make_samples.py
"""
import io
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent / "samples"
OUT.mkdir(exist_ok=True)
BUYER = ("Acme Retail India Pvt Ltd", "07AAACA0001A1Z9", "Plot 12, Okhla Phase II, New Delhi 110020")
W, H = A4


def inr(x: float) -> str:
    """Indian digit grouping: 390000 -> 3,90,000.00"""
    neg = x < 0
    s = f"{abs(x):.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + "." + frac


# --------------------------------------------------------------- layout A
def layout_a(path, vendor, gstin, addr, inv_no, date, po, lines, gst_rate=0.18, show_po=True):
    """Clean tabular invoice, GST shown separately (CGST+SGST)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, H - 50, vendor)
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 65, addr)
    c.drawString(40, H - 78, f"GSTIN: {gstin}")
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(W - 40, H - 50, "TAX INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(W - 40, H - 70, f"Invoice No: {inv_no}")
    c.drawRightString(W - 40, H - 84, f"Invoice Date: {date}")
    if show_po:
        c.drawRightString(W - 40, H - 98, f"PO Number: {po}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, H - 120, "Bill To:")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 134, BUYER[0])
    c.drawString(40, H - 148, BUYER[2])
    c.drawString(40, H - 162, f"Buyer GSTIN: {BUYER[1]}")
    y = H - 200
    c.setFont("Helvetica-Bold", 10)
    for x, t in [(40, "Description"), (330, "Qty"), (400, "Rate"), (490, "Amount")]:
        c.drawString(x, y, t)
    c.line(40, y - 4, W - 40, y - 4)
    c.setFont("Helvetica", 10)
    sub = 0
    for d, q, r in lines:
        y -= 18
        amt = q * r
        sub += amt
        c.drawString(40, y, d)
        c.drawString(330, y, str(q))
        c.drawString(400, y, inr(r))
        c.drawString(490, y, inr(amt))
    tax = round(sub * gst_rate, 2)
    y -= 30
    for label, val in [("Sub Total", sub), (f"CGST @ {gst_rate*50:g}%", tax / 2),
                       (f"SGST @ {gst_rate*50:g}%", tax / 2), ("Grand Total", sub + tax)]:
        c.drawString(360, y, label)
        c.drawString(490, y, inr(val))
        y -= 16
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(40, 60, "Payment due within 30 days. Bank: HDFC Bank, A/c 50200012345678, IFSC HDFC0000123")
    c.save()


# --------------------------------------------------------------- layout B
def layout_b(path, vendor, gstin, addr, inv_no, date, po_text, lines, gst_rate=0.18):
    """Letter-style invoice, IGST, 'Amount Due', PO referenced in prose."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Times-Bold", 14)
    c.drawCentredString(W / 2, H - 50, vendor.upper())
    c.setFont("Times-Roman", 9)
    c.drawCentredString(W / 2, H - 64, addr + "  |  GSTIN " + gstin)
    c.line(40, H - 72, W - 40, H - 72)
    c.setFont("Times-Roman", 11)
    c.drawString(40, H - 100, f"Bill No.: {inv_no}")
    c.drawString(40, H - 115, f"Dated: {date}")
    c.drawString(40, H - 140, f"To, {BUYER[0]} (GSTIN {BUYER[1]})")
    if po_text:
        c.drawString(40, H - 160, po_text)
    y = H - 195
    c.setFont("Times-Bold", 11)
    c.drawString(40, y, "Particulars")
    c.drawString(320, y, "Qty")
    c.drawString(390, y, "Unit Price")
    c.drawString(480, y, "Total")
    c.setFont("Times-Roman", 11)
    sub = 0
    for d, q, r in lines:
        y -= 18
        sub += q * r
        c.drawString(40, y, d)
        c.drawString(320, y, str(q))
        c.drawString(390, y, inr(r))
        c.drawString(480, y, inr(q * r))
    tax = round(sub * gst_rate, 2)
    y -= 30
    c.drawString(330, y, "Taxable Value")
    c.drawString(480, y, inr(sub))
    c.drawString(330, y - 16, f"IGST @ {gst_rate*100:g}%")
    c.drawString(480, y - 16, inr(tax))
    c.setFont("Times-Bold", 11)
    c.drawString(330, y - 34, "Amount Due")
    c.drawString(480, y - 34, inr(sub + tax))
    c.save()


# --------------------------------------------------------------- layout C
def layout_c(path, vendor, gstin, addr, inv_no, date, po, lines, gst_rate=0.18):
    """Retail-style invoice where prices are INCLUSIVE of GST."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFillColorRGB(0.1, 0.3, 0.5)
    c.rect(0, H - 80, W, 80, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, H - 45, vendor)
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 62, f"{addr}  GSTIN {gstin}")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 110, f"Invoice # {inv_no}")
    c.drawString(40, H - 125, f"Date: {date}")
    c.drawString(40, H - 140, f"Purchase Order: {po}")
    c.drawString(320, H - 110, f"Customer: {BUYER[0]}")
    y = H - 180
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Item")
    c.drawString(320, y, "Qty")
    c.drawString(390, y, "Price")
    c.drawString(480, y, "Amount")
    c.setFont("Helvetica", 10)
    tot = 0
    for d, q, r_pre in lines:
        r = round(r_pre * (1 + gst_rate), 2)
        y -= 18
        tot += q * r
        c.drawString(40, y, d)
        c.drawString(320, y, str(q))
        c.drawString(390, y, inr(r))
        c.drawString(480, y, inr(q * r))
    y -= 30
    c.setFont("Helvetica-Bold", 11)
    c.drawString(330, y, "Total Payable")
    c.drawString(480, y, inr(tot))
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(40, y - 30, f"All prices are inclusive of GST @ {gst_rate*100:g}%.")
    c.save()


def scanify(src: Path, dst: Path, angle=0.8):
    """Turn a text PDF into an image-only 'scanned' PDF (no text layer)."""
    doc = pymupdf.open(src)
    pix = doc[0].get_pixmap(dpi=200)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    img = img.rotate(angle, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    random.seed(7)
    px = img.load()
    for _ in range(4000):  # scanner speckle
        x, y = random.randrange(img.width), random.randrange(img.height)
        px[x, y] = random.choice([90, 160])
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=200)
    dst.write_bytes(buf.getvalue())
    src.unlink()


V = {
    "bharat": ("Bharat Office Supplies Pvt Ltd", "07AABCB1234F1Z5", "45 Nehru Place, New Delhi 110019"),
    "nimbus": ("Nimbus Cloud Services LLP", "29AAFFN5678K1Z2", "Koramangala, Bengaluru 560034"),
    "shakti": ("Shakti Logistics", "27AAKFS9012L1Z8", "Bhiwandi, Thane, Maharashtra 421302"),
    "zenith": ("Zenith Traders", "07AAGFZ7777Q1Z1", "Chandni Chowk, Delhi 110006"),
    "apex": ("Apex Print Solutions", "24AACCA4321P1Z3", "Navrangpura, Ahmedabad 380009"),
}

if __name__ == "__main__":
    # 01 Happy path: clean PDF, explicit PO, exact match
    layout_a(OUT / "01_happy_bharat_furniture.pdf", *V["bharat"], "INV-1042", "02-Nov-2026", "PO-4501",
             [("Ergonomic office chair", 20, 8500), ("Height adjustable standing desk", 10, 22000)])

    # 02 EDGE: scanned image, no PO number, slight rate increase (within tolerance)
    tmp = OUT / "_tmp.pdf"
    layout_a(tmp, *V["shakti"], "SL/26-27/0318", "06-Nov-2026", "", [("Full truck load freight Mumbai-Pune", 12, 15200)],
             show_po=False)
    scanify(tmp, OUT / "02_edge_scanned_no_po_shakti.pdf")

    # 03 Split PO, milestone 1 (approve) then 04 milestone 2 over-bills remaining balance
    layout_b(OUT / "03_split_po_nimbus_milestone1.pdf", *V["nimbus"], "NCS-2026-0091", "20/10/2026",
             "Against your Purchase Order PO-4502 - Milestone 1 of 3 (Discovery & landing zone)",
             [("Cloud migration services - Milestone 1", 1, 200000)])
    layout_b(OUT / "04_edge_split_po_overbill_nimbus.pdf", *V["nimbus"], "NCS-2026-0117", "12/11/2026",
             "Against your Purchase Order PO-4502 - Milestones 2 & 3 (Workload migration, cutover)",
             [("Cloud migration services - Milestones 2 and 3", 1, 450000)])

    # 05 EDGE: disguised duplicate of #01 -- different layout, 'INV/1042', re-dated
    layout_b(OUT / "05_edge_duplicate_bharat.pdf", *V["bharat"], "INV/1042", "05/11/2026",
             "Ref: PO-4501 (re-sent, please process)",
             [("Ergonomic office chair", 20, 8500), ("Height adjustable standing desk", 10, 22000)])

    # 06 EDGE: GST-inclusive prices + unit price above PO beyond tolerance
    layout_c(OUT / "06_edge_price_variance_gst_inclusive.pdf", *V["bharat"], "INV-1077", "10-Nov-2026", "PO-4504",
             [("Laser toner cartridge 88A", 40, 3450)])

    # 07 Unknown vendor (not in vendor master)
    layout_a(OUT / "07_unknown_vendor_zenith.pdf", *V["zenith"], "ZT-559", "08-Nov-2026", "PO-4501",
             [("Ergonomic office chair", 20, 8500)])

    # 08 Blocked vendor
    layout_a(OUT / "08_blocked_vendor_apex.pdf", *V["apex"], "APS-2210", "09-Nov-2026", "PO-4999",
             [("Brochure printing (A4, 4 colour)", 5000, 12)])
    # 09 EDGE: impersonation -- known vendor name, but GSTIN (and bank) differ from master
    layout_a(OUT / "09_edge_gstin_mismatch_impersonation.pdf", "Bharat Office Supplies Pvt Ltd", "07AABCB9999X1Z2",
             "45 Nehru Place, New Delhi 110019", "INV-1090", "14-Nov-2026", "PO-4504",
             [("Laser toner cartridge 88A", 40, 3200)])
    print("\n".join(sorted(p.name for p in OUT.glob("*.pdf"))))
