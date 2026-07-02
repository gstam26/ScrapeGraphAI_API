# Filter

**Responsibility:** route acquired pages to the questions they can answer — cost control, never completeness control. Hard rule: may only route, never exclude a page (2026-06-15); empty routing falls back to all columns.

## Interface
- In: `filter_page(page: PageDoc, columns: [ColumnSpec], diag)` — `src/filter.py`
- Out: `RoutedPage(page, relevant_columns: set[str])`

## Current implementation
- Chunk page (~1000 chars on paragraph boundaries, ≤100 chunks), embed via Ollama, per-column score = max cosine over chunks (relevance is local — 2026-06-20).
- Column relevant if score ≥ `FILTER_THRESHOLD=0.55` OR keyword gate (column-name words >3 chars in page text) — dense+sparse OR logic (2026-06-19).
- `FILTER_MODE="passthrough"`: routes everything but still computes and logs scores.
- Ollama unreachable → route all columns (logged `fallback_all`).

## Known issues
- **`FILTER_MODE` is NOT workbook-overridable** (`_SUPPORTED_CONFIG_KEYS`, `src/io_excel.py:31`) — passthrough requires a manual config.py edit; the work laptop carries that edit uncommitted.
- 0.55 threshold was calibrated on plant-milk questions; on the ADLM task the scorer barely discriminates (~AUC-0.5) — passthrough in use pending recalibration.
- Stale comment in config.py still explains the old 0.35 value.
- nomic-embed similarity range is compressed (0.40–0.72) — keyword gate compensates; generic terms gate poorly.

## Open questions
- Recalibrate threshold on ADLM validation-run filter-log data (scores are logged even in passthrough — the data exists).
- Per-column thresholds (deferred 2026-06-20).
- Make FILTER_MODE workbook-overridable?
