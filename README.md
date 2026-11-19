# Invoice Agent — from PDF to decision (PS-1)

A vendor invoice PDF goes in. The agent reads it (even scanned ones), checks it against the
vendor master and PO register, and returns **APPROVED / NEEDS_REVIEW / REJECTED** with the reason,
every check it ran, and the next action for the AP team. Every run is logged for audit.

## Run it

```bash
pip install -r requirements.txt          # + tesseract for scanned PDFs:
                                         #   mac: brew install tesseract   ubuntu: apt install tesseract-ocr
python make_samples.py                   # (re)generate the demo invoices
python test_pipeline.py                  # replays the demo, checks all 9 decisions
streamlit run app.py
```

**Deploy (free, gives a live link):** push this folder to GitHub → share.streamlit.io → New app →
pick the repo, main file `app.py`. `packages.txt` installs Tesseract on the server automatically.

Optional: set `ANTHROPIC_API_KEY` (and `LLM_MODEL`) to let an LLM fill fields the parser missed.

## How it works

| # | Stage | What it does |
|---|-------|--------------|
| 1 | Ingest | Hash the file; flag an identical re-upload |
| 2 | Extract text | Text layer → rebuild rows from word positions. No text layer → OCR (Tesseract, 300 dpi) |
| 3 | Parse fields | Invoice no., date, PO, GSTIN, line items, subtotal/tax/total. Handles 3 layouts, CGST+SGST / IGST, GST-inclusive prices. LLM fallback only for missing fields |
| 4 | Validate | Required fields; lines = subtotal; subtotal + tax = total; GST is a real slab; date sane |
| 5 | Vendor check | Match by GSTIN, else by name. Unknown/blocked → reject. Name matches but GSTIN differs → hold |
| 6 | Duplicate check | Normalised invoice no. (`INV/1042` = `INV-1042`) → reject. Same total within 15 days → review |
| 7 | PO match | Explicit PO must exist and belong to the vendor. No PO → infer from the vendor's open POs (description + amount), a person confirms |
| 8 | Price & balance | Each line vs PO line: unit price ±2%, qty ≤ ordered. Invoice vs **remaining** PO balance (approved invoices only), tolerance 2% capped at ₹5,000 |
| 9 | Decision | Any reject-level failure → REJECTED; any review-level issue → NEEDS_REVIEW; else APPROVED + payment terms |

**UI:** live run view (each stage lights up as it runs), decision card with "why", PO comparison,
raw text and the original document. **Dashboard** with history, decision mix, top exception reasons,
₹ stopped/held, PO utilisation. **Review queue** where a person approves/rejects; overrides go into the audit trail.

## Design choices (talking points)

- **Deterministic first, LLM second.** Rules are free, instant, repeatable and auditable — what you want
  deciding payments. The LLM only fills gaps, and its output goes through the same validation.
- **Three outcomes, not two.** The agent auto-decides the clear cases and routes only real exceptions
  to a person, with the exact action to take (e.g. "ask for a ₹10,000 credit note").
- **Fail safe.** When unsure (inferred PO, unreadable GSTIN), it holds rather than pays. Rejections
  are reserved for things that are definitely wrong (duplicate, unknown/blocked vendor).
- **PO balance only counts approved invoices**, so items stuck in review don't block legitimate ones.
- **Rules live in one place** (`pipeline.py` top) so a finance team can change tolerances.

## Edge cases built

| Sample | Scenario | Expected |
|---|---|---|
| 02 | **Scanned image, no PO number, rate +1.3%.** OCR; vendor GSTIN unreadable → matched by name; PO inferred; price within tolerance | Needs review — confirm PO |
| 03 → 04 | **Split PO / milestone billing.** M1 ₹2L approved; M2+3 bills ₹4.5L against ₹4L remaining | Needs review — over-bill ₹50,000 |
| 05 | **Disguised duplicate** of 01: different layout, `INV/1042`, re-dated 3 days later | Rejected — duplicate |
| 06 | **GST-inclusive prices + price variance.** ₹4,071 incl. GST → ₹3,450 pre-tax vs PO ₹3,200 (+7.8%) | Needs review — credit note ₹10,000 |
| 09 | **Impersonation.** Known vendor name, different GSTIN | Needs review — verify by phone |
| 07 / 08 | Unknown vendor / blocked vendor | Rejected |

## Demo script (5 min)

Full narration: `DEMO_SCRIPT.md`. Hands-free clicking: `python autopilot.py [url]` (press ENTER to advance).


Click **Reset demo ledger** first. Run in this order (duplicates and balances depend on history):
1. **01** happy path → Approved (walk through stages, PO comparison).
2. **05** → Rejected as duplicate of 01, despite different format and number.
3. **03** then **04** → milestone 1 approved, milestone 2 over-bills the remaining balance.
4. **02** → scanned, no PO: show OCR raw text and the inferred PO.
5. **06** → GST-inclusive normalisation + price variance with exact credit-note amount.
6. **Review queue** → approve 02 with a note. **Dashboard** → history, ₹ held/stopped, PO utilisation.

## Assumptions

- Buyer is one company (Acme Retail India, GSTIN `07AAACA0001A1Z9`); currency INR; one GST rate per invoice.
- Two-way match (invoice ↔ PO). A goods-receipt check (three-way match) would be the next addition.
- Vendor master and POs are CSVs standing in for an ERP.

## What I'd build next

Email inbox intake; goods receipt (3-way match); bank-detail change detection; per-field
confidence from OCR; vendor-facing emails generated from the "next actions"; learning tolerances per vendor.
