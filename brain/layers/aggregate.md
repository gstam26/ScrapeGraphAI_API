# Aggregate

**Responsibility:** collapse per-page cells into one answer per entity × question for the Matrix, preserving every contribution for Provenance. Matrix reads aggregated `row.cells`; Summary/Provenance read raw `row.all_cells` (2026-06-22 split).

## Interface
- In: `aggregate_cells(cells: [ExtractedCell], list_columns: set[str])` — `src/aggregate.py`
- Out: one aggregated `ExtractedCell` per (entity, column): `value` = deduped display list, `evidence` ranked best-first, `has_conflict`, `num_sources`, `num_unique_values`, `source_urls`

## Current implementation
- Evidence dedup by (normalised value, quote, source_url); display-value fuzzy dedup at `_DEDUP_RATIO=85` token_sort_ratio (95→85 on 2026-06-29 for Oatly paraphrases).
- Null sentinel "None (not disclosed…)": never counts toward conflicts; shown only when no real value exists (2026-06-23).
- Conflict detection gated on question type: list columns (instruction contains list/comma-separated/deduplicated/for-each/one-per) never conflict; single-answer columns conflict on >1 unique value (2026-06-23).
- `_UNION_LIST_COLS` (currently `{"Plant milk types"}`): comma-split + union across sources into one canonical list.
- Evidence ranking: exact > fuzzy > none, then semantic_score desc.

## Known issues
- Contradictory comments in the file: header says `_DEDUP_RATIO` is "intentionally lower than AI_DEDUP_RATIO (95)", line ~159 says "same threshold as the eval's" — one is stale; check against `diagnostics/eval_lib/metrics.py`.
- Union logic is column-name-configured, not instruction-derived — new list-type columns must be added manually.
- Test fixture fragility precedent: near-identical short strings collide at ratio 85 (fixed 2026-07-01, fixture now uses distinct-topic strings).

## Open questions
- Should ADLM's "Diagnostics type" / "Recent news" become union columns for the 182 output? (Multiple pages each contribute partial lists — same shape as Plant milk types.)
