# ContraVault — Design & Engineering Report

This report explains the design decisions behind ContraVault, the domain reasoning that shaped them, and the results observed on a real tender. The goal is to show *why* the system is built the way it is, not just *what* it does.

---

## 1. The problem

Public-procurement evaluation asks three different questions about each vendor bid, and they are easy to wrongly merge into one:

1. Does the offer meet the technical specification?
2. Is the bidder even eligible to compete?
3. Among eligible bidders, who offers the lowest price?

A naive system treats all three as a single "score the vendor" task. That conflation is the central mistake ContraVault is designed to avoid, because the three questions differ in *kind*: one is fuzzy language understanding, one is a hard rule, and one is arithmetic.

The system was developed against a real Northern Coalfields Limited (NCL) GeM tender for swaged steel tubular poles conforming to IS:2713, evaluated against two vendor bundles (Vishal Heavy Engineering and Subham Poles).

---

## 2. Understanding the domain first

Several tender characteristics directly shaped the architecture:

- **Schedule-wise award.** The tender has three schedules (11 m / 410SP-57, 13 m / 410SP-72, 16 m / 410SP-80), and L1 is determined **separately for each schedule**. A vendor can win one schedule and lose another. This forced the matching unit to be `(vendor × schedule × requirement)` rather than per vendor.
- **Two-packet bid with reverse auction.** Technical and price are evaluated separately, and ranking uses H1 (highest-priced bid) elimination. This means price ranking is a distinct stage fed by sealed financial data — not something derivable from the technical bundle.
- **L1/L2 is decided by price, never by a certificate.** Registration certificates (Udyam/MSE, BIS license, local-content) only decide *eligibility to compete*. Among eligible bidders, lowest price wins. Encoding this correctly — certificates → eligibility gate → price ranking — is the difference between understanding the domain and doing surface-level text matching.
- **MSE + Make-in-India reservation.** Eligibility requires an MSE manufacturer that is a Class-I local supplier with ≥60% local content, valid Udyam registration, and a valid BIS Marking License covering IS:2713 Part 1–3.

---

## 3. Architectural decision: three separate engines

From the above, the system separates evaluation into three engines with deliberately different implementations:

| Engine | Question | Implementation | Why |
|---|---|---|---|
| Technical scoring | Does the offer meet each spec? | LLM + retrieval | Requires language understanding of specs and test reports; fuzzy. |
| Eligibility gate | Is the bidder qualified? | Deterministic rules | Pass/fail; must be auditable and must not be subject to LLM judgment. |
| Price ranking | Who is lowest per schedule? | Pure numeric | Arithmetic; an LLM has no business "scoring" a price. |

Three consequences of this separation:

- The LLM never touches price, eliminating a whole class of hallucination risk in the most consequential output.
- A high technical score cannot rescue a vendor who fails a gating eligibility rule — the gate is enforced independently.
- Each engine can be tested in isolation (and is — see §6).

---

## 4. The two correctness traps

Two specific failure modes were identified during document analysis and deliberately guarded against. Both are visible in the running system.

### Trap 1 — a vendor silent on a schedule must not silently pass

Vendor 1 (Vishal) quoted the 11 m (SP-57) and 16 m (SP-80) poles but was **silent on the 13 m SP-72**, which is Schedule 2 — the largest schedule by value. A per-vendor average would have buried this. Because matching is per `(vendor × schedule × requirement)`, the engine instead flags Schedule 2 explicitly.

Observed output for Vishal, Schedule 2:
- `TECH_S2_003` → **missing** (score 0): the offered pole lengths are 11 m and 16 m; neither matches the required 13 m.
- `TECH_S2_004` → **missing** (score 0): the offered designations are SP-80 and SP-57; the requirement specifies SP-72.

Crucially the engine produces a *reason*, citing the evidence pages and naming the exact mismatch — not just a low number.

### Trap 2 — historical prices must not be treated as current quotes

Vendor 2 (Subham) included prices from **past** GeM contracts (₹25,440 / ₹29,450 / ₹37,462.5 for SP-57 / SP-72 / SP-80). These are past-performance evidence, not Subham's quote for the current tender. Feeding them into the price ranking would be a serious correctness error.

The guard is the `evidence_type` field on every vendor fact. Those historical prices are typed `prior_contract_price`, and the price engine consumes only `current_quote` values. In the running system, price ranking starts in an "awaiting quotes" state with an explicit warning that historical prices must not be used — the system refuses to invent a ranking from past data.

---

## 5. Pipeline

- **Stage A — Ingest.** PDFs are converted to text locally (PyMuPDF, with OCR fallback for scanned pages). Because the LLM provider is text-only, no PDF or image is ever sent to the model — only extracted text. Page-level source references are preserved for provenance.
- **Stage B — Extract requirements.** The tender is decomposed into atomic, individually checkable requirements, each bucketed `technical` / `eligibility` / `commercial`, tagged to a schedule, and marked gating or not.
- **Stage C — Extract vendor evidence.** Every relevant vendor fact is extracted with an `evidence_type` and a source reference.
- **Stage D — Match & score.** Per `(vendor × schedule × requirement)`, the three engines run.
- **Stage E — Output.** A per-schedule compliance table (verdict, score, reason, source), per-vendor eligibility verdicts with failed conditions named, and a per-schedule L1/L2 ranking once current quotes are supplied.

A second input mode loads pre-structured JSON directly into the same `Requirement`/`Evidence` models, bypassing extraction entirely. This is used for fast, API-light testing; both modes converge on identical internal objects, so downstream logic is format-agnostic.

---

## 6. Results

On the sample tender and two vendors, in structured mode:

- **Requirements:** 28 extracted — 15 technical, 5 eligibility (gating), 8 commercial — each tagged to a schedule with expected values and source.
- **Eligibility:** both vendors QUALIFIED, with every check explained. Notably, the BIS-license check compared each certificate's validity (`2024-08-21` for Vishal, `2027-12-13` for Subham) against the tender opening date (`2024-06-06`) and passed both — demonstrating date-aware validation rather than mere presence checking.
- **Technical scoring:** average 86/100 for Vishal, 21 full-compliance items, 2 missing — the 2 missing being precisely the SP-72 Schedule 2 items (Trap 1). One requirement was scored `partial` where the test report showed an ambiguous length, showing the engine distinguishes partial from full rather than passing blindly.
- **Price ranking:** with current quotes entered (Vishal ₹26,000, Subham ₹27,000 for Schedule 1), the engine produced L1 = Vishal, L2 = Subham, and flagged Subham as H1-eliminated — correctly implementing the reverse-auction rule. Historical prices were never used (Trap 2).

---

## 7. Testing

The engines are covered by isolated tests:

- `test_eligibility.py` — gating rules, including disqualification on a failed condition.
- `test_evidence_typing.py` — that a `prior_contract_price` is not consumable as a `current_quote`.
- `test_price_ranking.py` — per-schedule sorting, L1/L2, H1 elimination.
- `test_structured.py` — that structured JSON maps to the same internal schemas the PDF path produces.

The full suite passes, which also serves as a regression guard: it proves the LLM-provider swap and the structured-input addition left the engines untouched.

---

## 8. Engineering decisions worth noting

- **Provider abstraction behind a single client.** Swapping the LLM provider (Gemini → Groq) touched only `llm/client.py` and `config.py`; the pipeline and engines call a stable client interface and were unaffected.
- **Rate-limit resilience.** The client throttles between calls and retries on HTTP 429 with back-off, honoring `Retry-After`. On total failure it returns a typed error the scoring layer records, rather than crashing the pipeline — a deliberate choice so one bad call doesn't lose a whole run.
- **Provenance everywhere.** Requirements and evidence both carry source references, so every cell in the output table is traceable to a page. This is what makes the output defensible to a human evaluator.

---

## 9. Limitations and next steps

- **PDF extraction quality** bounds the technical scoring. Heavily scanned or poorly formatted bundles depend on OCR accuracy; the structured-input mode exists partly to test the engines independently of extraction quality.
- **Current quotes are an explicit input.** Because real bid prices live in the sealed financial packet, the system asks for them rather than guessing — by design, not as a gap.
- **Free-tier API limits** constrain large PDF runs; the technical-scoring stage is the main consumer. Batching multiple requirements per LLM call and prompt caching would cut request volume and are natural next steps.
- **Future work:** confidence scores on extracted evidence, side-by-side multi-vendor diff views per requirement, and an audit-log export for compliance records.

---

## 10. Summary

ContraVault's central idea is that tender evaluation is three different problems, not one, and that the most consequential outputs (eligibility and price) must be deterministic and auditable while only the genuinely fuzzy part (technical compliance) uses an LLM. The two traps — catching a vendor who skipped a schedule, and refusing to treat historical prices as live quotes — demonstrate domain understanding beyond surface text matching, and both are observable in the running system with reasons and source references attached.
