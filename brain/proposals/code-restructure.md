# Proposal: Code restructure (propose-only — nothing executed)

**Status:** Written 2026-07-02 from a full read of `src/`, root modules, `tests/`, `diagnostics/`. For George's review after the validation cycle. Every item below is flagged **pure move** (behaviour-identical, import-path changes only) or **behaviour-adjacent** (could change runtime behaviour — needs its own test pass).

## Current structure assessment

The big past complaints (PROJECT_STRUCTURE.md) are already fixed: layers live in `src/`, tests in `tests/`, `LLMAPI` in `src/llmapi.py`, acquire models renamed `acquire_models.py`. What remains is clutter, not confusion:

**Good, keep as-is:** `src/` layer layout; `src/acquire/` and `src/resolve/` sub-packages; config/models/entry-points at root; plug-in dispatch pattern; `tests/` naming.

**Actual problems, by weight:**
1. **Root-level script sprawl** — 6 one-shot scripts at root (`adlm_scraper.py`, `resolve_urls.py`, 4× `build_*_workbook.py`) sit beside the 4 real entry-point/schema modules, obscuring what the *product* is. Worst offender for a consultant opening the repo.
2. **Dead config flags** — `CRAWL_ENABLED`, `FETCH_WAIT_MS`, `ENABLE_COST_TRACKING`, `ENABLE_LATENCY_TRACKING`, `ENABLE_PROVENANCE` are read by nothing (audit §F). Dead flags in config are actively misleading: someone will toggle `CRAWL_ENABLED` and expect an effect.
3. **Dead model aliases** — `EvidenceItem`, `CellContribution` (`models.py:77,106`): zero references.
4. **Superseded diagnostics** — `crawl_trace.py`, `crawl_trace_embed.py`, `crawl_collect.py`, `crawl_debug.py` already ruled outdated/duplicate by `diagnostics/DIAGNOSTICS_INVENTORY.md` §3.
5. **`eval_lib` buried in `diagnostics/`** — the Stage 10 evaluation framework is the dissertation's core contribution, filed under a directory whose inventory calls half its contents throwaway. Placement undersells it and its one test (`test_aligner_group_credit.py`) hides outside `tests/`.
6. **`io_excel.py` is two modules** — ~600 lines mixing input parsing (`read_input`, sheet readers) with output writing (7 sheet writers + styling). Both change for different reasons.
7. **Stale comments** recorded in audit §E (FILTER_THRESHOLD 0.35 note; `_DEDUP_RATIO` self-contradiction; `FETCH_BACKEND` "dev default" note).

## Proposed changes

| # | Change | Kind | Risk | Notes |
|---|---|---|---|---|
| R1 | Move `adlm_scraper.py`, `resolve_urls.py`, `build_*_workbook.py` → `scripts/` | Pure move | **Low** | Update relative-path constants inside them (`matched_official_urls.csv`, `adlm-inputs/…`) to be repo-root-relative or run-from-root documented. No prod imports touch them |
| R2 | Archive `diagnostics/{crawl_trace,crawl_trace_embed,crawl_collect,crawl_debug}.py` → `diagnostics/archive/` | Pure move | **Low** | Inventory already recommends it; keeps design-evolution evidence for the dissertation appendix |
| R3 | Delete dead config flags (5) + dead model aliases (2) | Behaviour-adjacent in principle, none in practice | **Low** | Grep-verified zero readers. `Config.fetch_wait_ms` field also unused — remove together. One caveat: external notebooks/scripts not in the repo could import the aliases |
| R4 | Promote `diagnostics/eval_lib/` → `src/eval/`; move its fixtures with it; move `test_aligner_group_credit.py` → `tests/` | Pure move | **Medium** | Touches imports in `eval_extraction.py`, tests, and the .gitignore fixtures exception path. Do NOT rename modules (aligner/metrics/gt_reader names appear in the dissertation text) |
| R5 | Split `src/io_excel.py` → `src/io_excel/reader.py` + `writer.py` (package with re-exporting `__init__`) | Pure move | **Medium** | Re-export `read_input`/`write_output_excel` from `__init__` so `main.py`/tests need no changes. Defer if it competes with run-critical work |
| R6 | Fix stale comments (audit §E) while touching config for R3 | Comment-only | **Low** | Resolve the `_DEDUP_RATIO` contradiction by checking `eval_lib/metrics.py`'s actual AI_DEDUP_RATIO first |
| R7 | *(Considered, rejected)* move `pipeline.py`/`models.py`/`config.py` into `src/` | — | — | Churn without payoff: every import in src/, tests/, diagnostics/ changes; root entry-point + shared-schema is a fine convention and `sys.path` tricks in tests depend on it |

**Nothing above changes pipeline behaviour** except R3's caveat, and R1's path constants (mechanical, verified by running each script's `--help`/dry path once).

## Suggested execution order

1. **R3 + R6** (dead flags/aliases + comments) — one commit, smallest blast radius, immediately de-confuses config.
2. **R2** (archive diagnostics) — one commit, zero imports.
3. **R1** (scripts/) — one commit; run `python scripts/build_182_workbook.py` once to verify path constants.
4. **R4** (eval promotion) — after the 182 run ships (it touches the dissertation-critical evaluation code; don't move it while it might be needed at short notice).
5. **R5** (io_excel split) — optional; only if further output-schema work (char_span column, verify tiers) is planned, in which case do it *first* as the enabling refactor.

Each step: full offline suite (`python -m pytest tests/ --ignore=tests/test_acquire_smoke.py`, 72 green today) before and after.

## Decision needed (George)
- Approve/trim the list — especially R4 timing (before vs after dissertation submission) and whether R5 happens at all.
- R3 caveat: confirm no out-of-repo notebooks import `EvidenceItem`/`CellContribution`.
