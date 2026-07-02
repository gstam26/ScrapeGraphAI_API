# Verify

**Responsibility:** deterministic check that each quote actually appears in the cached source page. The pipeline's trust anchor — this is why Acquire must save markdown (2026-06-15, SGAI rejection).

## Interface
- In: `verify_cells(cells, page: PageDoc, diag)` — `src/verify.py`
- Out: same cells; per evidence item: `verified`, `verification_score`, `char_span`, `match_type` (exact/fuzzy/fuzzy_soft/none), `semantic_score`. Cell verified = all quoted evidence verified.

## Current implementation
- Gate chain: exact substring (100.0, char_span) → markdown/whitespace-normalised `partial_ratio ≥ 70` → soft ≥ 68 for quotes ≥ 100 chars whose first/last 20 chars appear literally (Options A + C, plant-milk cycle v4).
- Diagnostic (NOT a gate): `semantic_score` = Ollama cosine(value, quote), batched, one call per page's eligible evidence. Skipped gracefully if Ollama unreachable.
- Deliberately deterministic — LLM keep/reject verification rejected 2026-06-24 (reproducibility of the verify→score chain is the dissertation's trust claim).

## Known issues
- **Entailment gap:** verified = quote exists, NOT quote supports the value. The gap consultants would care about. Addressed by `proposals/semantic-verify.md` (Phase A: tier the existing semantic_score; Phase B: proxy LLM judge gated on a human-labelled eval; annotation only, never a silent gate).
- `char_span` computed but not written to the Provenance sheet (report-deltas §5.5) — one io_excel column to fix.
- Known FN: Oatly GHG table-caption quote (markdown rendering defeats fuzzy+anchors). Single case, not generalised.

## Open questions
- Phase A thresholds need the labelled eval set (~100 ADLM pairs, George to label) before anything ships.
