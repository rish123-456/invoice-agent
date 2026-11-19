# 5-minute demo video script

**Setup:** start the app (or open the deployed URL), run `python autopilot.py <url>`, start Loom
recording the browser window, keep the terminal off-screen. Press **ENTER** where it says ⏎.
About 700 words ≈ 5 minutes at a calm pace. Don't read word for word; hit the bold points.

---

### 0:00 – Intro *(app open on "Process invoice")* · ~30 s
Hi, I'm Rishabh, and I picked **PS-1: invoice processing**.
An AP clerk gets hundreds of vendor PDFs a month. For each one they find the PO, check the numbers,
and decide. It's slow and it's where expensive mistakes happen: duplicates paid twice, vendors
over-billing a PO.
I built an agent that takes **an invoice PDF in** and gives **a decision out**: approved, needs review,
or rejected, with the reason and the next action. Everything in between is visible.

⏎ **Happy path**

### 0:30 – Happy path (01) · ~45 s
This is a clean invoice from Bharat Office Supplies. Watch the run on the right; there are nine stages.
It **extracts text**, **parses** invoice number, date, PO, GSTIN and line items, then **validates**:
the lines add up to the subtotal and subtotal plus GST equals the total.
It finds the **vendor by GSTIN** in the vendor master, checks for **duplicates**, matches **PO-4501**,
and compares **every line's price and quantity** to the PO.
Everything passes, so it's **approved** and scheduled on the vendor's 30-day terms.
Under "Why" you can see every check it ran. That's the audit trail.

⏎ **Duplicate**

### 1:15 – Edge 1: disguised duplicate (05) · ~30 s
Now the same vendor sends this invoice: a **different layout**, a different date, and the number is
**INV slash 1042** instead of INV dash 1042. A tired person would pay it twice.
The agent **normalises invoice numbers**, sees it's the same as the one we just approved, and
**rejects** it with a reference to the original run.

⏎ **Milestone 1**

### 1:45 – Edge 2: split PO (03 → 04) · ~45 s
Next, a cloud migration PO worth six lakh, billed in **milestones**. Milestone one, two lakh,
is approved.
⏎
Now the vendor bills milestones two and three together: **four and a half lakh**. Only four lakh is
left on the PO. The agent tracks the **remaining balance from approved invoices**, sees a fifty
thousand over-bill, which is above my tolerance, and **holds** it. The action is specific: approve
four lakh, or get the PO amended.

⏎ **Scanned invoice**

### 2:30 – Edge 3: scanned, no PO (02) · ~45 s
This one is a **scanned image**. There's no text layer, so the agent runs **OCR**.
It OCRs two cleaned versions of the scan and keeps whichever reads more fields. It identifies the
vendor by GSTIN, and if the GSTIN were too blurry it would fall back to **matching by name**.
The real problem: there's **no PO number**. So it looks at that vendor's open POs and **infers** the
freight PO from the line description and amount. The rate is 1.3% higher than the PO, which is
within my 2% tolerance.
It could pay this, but it **inferred** the PO, so it doesn't auto-approve. It asks a person for a
**one-click confirmation**. When unsure, hold; don't pay.

⏎ **GST-inclusive**

### 3:15 – Edge 4: GST-inclusive + price variance (06) · ~40 s
This vendor prints prices **including GST**: 4,071 per toner. Compared directly with the PO price
of 3,200, that looks 27% high, which is wrong.
The agent spots "inclusive of GST", **backs out the 18%**, gets 3,450 pre-tax, and the real variance
is **7.8%**. It then tells AP exactly what to do: **ask for a ten-thousand-rupee credit note**.

⏎ **Review queue**

### 3:55 – Review queue · ~20 s
Exceptions go to a **review queue**. Here I confirm the inferred PO on the scanned invoice with a
note and approve it. The **human decision is logged** in that run's audit trail.

⏎ **Dashboard**

### 4:15 – Dashboard · ~25 s
The dashboard shows **every run**, the decision mix, **why invoices didn't auto-approve**, and how
much money was **stopped or held**.
⏎
And **PO utilisation**: how much of each PO is billed, so you can see the milestone PO's balance.

⏎ **Close**

### 4:40 – Design choices · ~20 s
Two choices I'd highlight. **Rules first, AI second**: deterministic checks decide payments because
they're repeatable and auditable, and an LLM only fills fields the parser missed. **Three outcomes**:
it auto-decides the clear cases and sends only real exceptions to a person, with the exact next step.
Next I'd add email intake and goods-receipt three-way matching. Thanks!

---

**If something breaks while recording:** stop, click "Reset demo ledger", and start again.
Order matters, because duplicates and PO balances depend on earlier runs.
