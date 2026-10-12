# MyVault — Lender Intelligence OS · Sovereign Demo

A self-contained, **offline** demonstration of the core wedge: take a borrower's
data, run a deterministic credit-policy engine, produce an **explainable
decision** with **maker-checker** control and a **full audit trail** — all
running inside one container. **No data leaves the container. No internet. No API keys.**

This is the artifact to hand a prospective lender so they can click through the
idea in two minutes, on their own machine, without sending a single byte to a cloud.

---

## Run it (two options)

### Option A — Docker Compose (recommended)
```bash
docker compose up --build
```
Then open **http://localhost:8080**

### Option B — Docker
```bash
docker build -t myvault-demo .
docker run -p 8080:8080 myvault-demo
```

### No Docker? Run it directly (Python 3.8+)
```bash
pip install pdfplumber      # only needed for real PDF parsing
python3 app.py
```
Then open **http://localhost:8080**. The core (decisions, CSV, maker-checker, audit)
runs on the standard library alone; **PDF parsing** needs `pdfplumber`. Docker installs
it for you. Designed for **laptop / desktop** — the platform with full capability.

To stop: `Ctrl+C`, or `docker compose down`.

---

## What to show a prospect (90-second script)

1. **Pick a borrower** (Ravi → clean, Anita → borderline, Mohammed → weak).
2. Click **Run MyVault decision**. The engine returns **APPROVE / REFER / DECLINE**
   with the numbers behind it — FOIR, LTV, bureau, banking conduct.
3. Read the **explainable reasons** — every flag is a plain-language, adverse-action-style
   line a credit officer (or a regulator) can audit.
4. **Maker-checker**: confirm the agent, or override it — an override **forces a written
   reason**. This is the control regulated lenders require.
5. Watch the **audit trail** capture every step with a timestamp and actor.
6. **Ingest a real PDF** — click *Load sample PDF* (or drop your own bank-statement PDF),
   then *Run decision on parsed statement*. It parses the document to structured data on
   the machine and drives a live decision. Point out *"no data left this laptop."*
7. **Ingest a CSV** as well, if you want to show the simpler path.

To prove sovereignty live: uncomment `network_mode: none` in `docker-compose.yml`
and run again. It still works with the container fully air-gapped.

---

## What is real vs. demo-grade (be honest with prospects)

| Component | This demo | The real product |
|---|---|---|
| Deployment | One container, offline | Same posture, hardened: on-prem / VPC / appliance |
| Decision engine | Deterministic rules (real logic) | Same engine + lending context graph + fine-tuned local model for extraction |
| Document parsing | Real **PDF parsing** (digital PDFs, via pdfplumber) + CSV | Add a local VLM/OCR stack (Docling, PaddleOCR-VL, Qwen2.5-VL) for scans, handwriting, multilingual KYC, legal/rent docs |
| Data | 3 sample borrowers | Live connectors to LOS / LMS / core / bureau |
| Maker-checker & audit | Real, working | Same + RBAC, consent, retention, DPDP posture |

The decision logic, maker-checker control, and audit trail are genuinely working —
not mocked. What is simplified is the parsing (rules instead of a model) and the
data (samples instead of live connectors). Say so plainly; credibility is the product.

---

## Files
```
app.py                       # server + policy engine (stdlib)
docparser.py                 # PDF -> structured data (pdfplumber)
requirements.txt             # pdfplumber
gen_sample_pdf.py            # regenerates the sample PDF
static/index.html            # single-page UI
data/borrowers.json          # sample borrower profiles
data/sample_bank_statement.csv
data/sample_bank_statement.pdf
Dockerfile / docker-compose.yml
```

*Sample data only. Not a credit decision for any real applicant.*
