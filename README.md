# ContraVault

**Tender ↔ Vendor Compliance & Scoring Engine**

ContraVault is a document-intelligence system for public-procurement evaluation. It ingests a government tender and one or more vendor bid bundles, extracts the tender's requirements and each vendor's evidence, scores compliance requirement-by-requirement with written reasons, applies a pass/fail eligibility gate, and ranks qualified vendors by price per schedule.

It was built and validated against a real Northern Coalfields Limited (NCL) GeM tender for swaged steel tubular poles (IS:2713) and two vendor bundles.

---

## Why it exists

Evaluating a government tender against vendor bids is slow, error-prone manual work. Requirements are buried across long PDFs, vendor bundles are heterogeneous (scanned certificates, bilingual contracts, test reports, financials), and the rules for *who wins* are subtle. ContraVault automates the comparison while keeping every decision explainable and traceable to a source page.

---

## Core design: three separate engines

The single most important design decision is that ContraVault does **not** treat evaluation as one big scoring problem. It splits into three concerns that are genuinely different in nature:

1. **Technical scoring** — *Does the vendor's offer meet each technical requirement?* Handled by an LLM with retrieval. Produces a compliance verdict (`full` / `partial` / `missing` / `not_addressed`), a score, and a **written reason** for every requirement.
2. **Eligibility gate** — *Is the vendor qualified to compete at all?* Handled by deterministic rules (no LLM). A vendor failing any gating condition is disqualified regardless of technical score.
3. **Price ranking** — *Among qualified vendors, who is lowest per schedule?* Pure numeric comparison (no LLM). Determines L1 / L2 and applies H1 (highest-priced bid) elimination per the tender's reverse-auction rule.

Keeping these separate means the LLM never "scores" price, a strong technical match can't override a failed eligibility check, and the price logic stays auditable.

---

## Key features

- **Per-schedule scoring** — matching is keyed on `(vendor × schedule × requirement)`, because government tenders are awarded schedule-wise. A vendor can qualify for some schedules and miss others, and the system surfaces exactly that.
- **Evidence provenance typing** — every extracted vendor fact carries an `evidence_type` (`current_quote`, `prior_contract_price`, `certificate`, `self_declaration`, `test_report`, `financial`) and a source reference. This prevents a historical contract price from being mistaken for a current bid.
- **Certificate validity checking** — eligibility checks a certificate's validity against the **tender opening date**, not merely whether the document is present.
- **Explainable output** — every score has a one-sentence reason and a source page; every eligibility verdict names its failed conditions.
- **Two input modes** — PDF upload (the real workflow: extract → embed → retrieve → reason) and a structured JSON mode for fast, API-light testing. Both converge on the same internal models.

---

## Architecture

```
                 ┌─────────────────────────────┐
   Tender PDF ──▶│  Stage A: Ingest (local      │
   Vendor PDFs ─▶│  text/OCR extraction)        │
                 └──────────────┬──────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            ▼                                        ▼
  Stage B: extract requirements        Stage C: extract vendor evidence
  (atomic, bucketed, per-schedule)     (typed by evidence_type + source)
            └───────────────────┬───────────────────┘
                                ▼
                  Stage D: match (vendor × schedule × requirement)
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                         ▼
  Engine 1: Technical    Engine 2: Eligibility    Engine 3: Price
  (LLM + retrieval)      (deterministic gate)     (numeric, per schedule)
        └───────────────────────┴───────────────────────┘
                                ▼
              Stage E: per-schedule compliance table,
              eligibility verdicts, L1/L2 ranking, export
```

---

## Tech stack

- **UI:** Streamlit
- **LLM:** Groq (`llama-3.1-8b-instant`) via the OpenAI-compatible API — text-only, with throttling and 429 retry/back-off
- **Retrieval:** sentence-transformers embeddings (`BAAI/bge-m3`) + vector store
- **PDF parsing:** PyMuPDF with OCR fallback (pytesseract) for scanned pages
- **Schemas:** Pydantic models (`Requirement`, `Evidence`, scoring models)

---

## Project structure

```
contravault/
├── app.py                       # Streamlit UI
├── config.py                    # provider/model config (Groq)
├── requirements.txt
├── llm/
│   └── client.py                # Groq client (OpenAI-compatible) + retry/backoff
├── models/
│   ├── requirement.py           # Requirement schema
│   ├── evidence.py              # Evidence schema (with evidence_type)
│   └── scoring.py               # compliance score schema
├── pipeline/
│   ├── ingest.py                # Stage A
│   ├── extract_requirements.py  # Stage B
│   ├── extract_evidence.py      # Stage C
│   ├── engine_technical.py      # Engine 1 (LLM)
│   ├── engine_eligibility.py    # Engine 2 (deterministic)
│   └── engine_price.py          # Engine 3 (numeric)
├── retrieval/
│   ├── embeddings.py
│   └── vector_store.py
├── utils/
│   ├── pdf_utils.py             # local PDF → text/markdown
│   ├── structured_loader.py     # JSON/markdown bypass loader
│   └── date_utils.py
└── tests/
    ├── test_eligibility.py
    ├── test_evidence_typing.py
    ├── test_price_ranking.py
    └── test_structured.py
```

---

## Setup

Requires Python 3.12.

```bash
git clone <your-repo-url>
cd contravault
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

Set a Groq API key (free, no card, from console.groq.com):

```bash
set GROQ_API_KEY=your_key_here   # Windows
# export GROQ_API_KEY=your_key   # macOS / Linux
```

You can also paste the key into the sidebar at runtime.

---

## Run

```bash
streamlit run app.py
```

Then in the browser:

1. Enter your **Groq API key** in the sidebar.
2. Set the **Tender Opening Date** (used for certificate-validity checks). For the sample data this is `2024-06-06`.
3. Choose **Input Mode**:
   - **PDF** — upload the tender PDF and vendor PDFs.
   - **Structured (JSON)** — upload `tender.json` and one vendor JSON per vendor (fast, minimal API use for extraction).
4. Click **Process Documents**.
5. Walk the tabs: Requirements → Vendor Evidence → Technical Scores → Eligibility → Price Ranking → Export.

For price ranking, enter each qualified vendor's **current** quote per schedule (historical prices from past contracts must not be used) and click Calculate Rankings.

---

## Tests

```bash
pytest tests/
```

The suite covers the eligibility gate, evidence-type safety, price ranking, and structured-input mapping.

---

## Notes

- The structured JSON sample files are derived from the source tender and vendor documents; they are a faithful representative test set, not a byte-perfect transcription of the PDFs.
- The free Groq tier is rate-limited; the client throttles and retries on 429. For large PDF runs, expect the technical-scoring stage to be the main consumer of API calls.
