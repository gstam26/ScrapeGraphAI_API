# Changelog

Notable changes, newest first. Full rationale for each decision: `brain/decision-log.md`.

## 2026-07-09

### Repo restructure (executes brain/proposals/code-restructure.md R1/R2/R3/R6)
- One-shot scripts moved off the root: `adlm_scraper.py`, `resolve_urls.py`, 4× `build_*_workbook.py` → `scripts/` (run from repo root; the two that import `src.*` gained a sys.path shim). Behaviour-identical.
- Superseded crawl diagnostics archived: `crawl_trace.py`, `crawl_trace_embed.py`, `crawl_collect.py`, `crawl_debug.py` → `diagnostics/archive/` (per DIAGNOSTICS_INVENTORY §3).
- Dead config flags deleted (`CRAWL_ENABLED`, `FETCH_WAIT_MS`, `ENABLE_COST_TRACKING`, `ENABLE_LATENCY_TRACKING`, `ENABLE_PROVENANCE` + unused `Config.fetch_wait_ms`); dead model aliases deleted (`EvidenceItem`, `CellContribution`). Grep-verified zero readers.
- Stale comments fixed (audit §E): FILTER_THRESHOLD 0.35 note, FETCH_BACKEND "dev default" note, `_DEDUP_RATIO` inline contradiction (header comment was correct: 85 is intentionally below eval's AI_DEDUP_RATIO 95).
- Stale `PROJECT_STRUCTURE.md` deleted (marked stale since 2026-07-02; history in git).
- R4 (eval_lib promotion) and R5 (io_excel split) deliberately deferred — summary eval in flight.

## 2026-07-02

### Performance & robustness (commit 36d7272)
- **Entity-level parallelism**: `run_pipeline` processes URL specs concurrently (`PIPELINE_ENTITY_WORKERS=4`); per-spec diagnostics accumulate locally and merge in original order (deterministic sheets). One spec = one domain, so per-domain request rates are unchanged. Projected 182-company wall clock ~50–70 min (was ~4.5 h measured pro rata).
- **Global LLM-call cap**: `EXTRACT_MAX_CONCURRENT_CALLS=16` semaphore across all entity/page/chunk workers.
- **LLMAPI 5xx retry**: one retry on Power Automate proxy 5xx (a 502 silently blanked a chunk's cells in the validation run). Timeouts/4xx unchanged.
- **Locale-variant link dedup** (`CRAWL_LOCALE_DEDUP=True`): translated copies of already-fetched pages (`/fr.html`, `/ko_kr.html`, `/de/de`) are dropped from the crawl — the validation run spent up to 12/15 of a page budget on them. Pattern-based, no site list.
- **Score-aware link cap**: `CRAWL_MAX_LINKS_PER_PAGE` now keeps the top-30 by relevance score instead of the first-30 in DOM order (closes the 2026-07-01 known issue — footer About/locations links past the 30th anchor now reach the scorer).
- ⚠️ The two crawl changes require re-validation on the 25-company sample before the 182 production run.

### Docs & tests
- `brain/` audited against live code and restructured (README index, per-layer notes, task file, proposals dir); tool register corrected (extract default is `azure`, `llmapi` is the per-run production override; Claude direct-API path documented as off-network only).
- Three investigation proposals: depth-1 runtime, Firecrawl replacement (pooled Playwright, blocked on IP-exposure decision), semantic verification (support tiers, never silently auto-pass).
- Root README rewritten (was describing the pre-restructure layout and an SGAI-only extract layer); this CHANGELOG added; `matched_official_urls.csv` (the 182-run URL input) now git-tracked via a `.gitignore` exception.
- `tests/test_resolve.py` pytest fixtures fixed (3 tests had errored on a missing fixture since the `tests/` move). Offline suite: 72 passing.

## 2026-07-01
- **Firecrawl raw-HTML link discovery** (commit 322d0ec): Firecrawl's markdown AND cleaned HTML drop some nav/footer links; discovery now reads `rawHtml` for the Firecrawl backend. Validated: Surmodics Q1 recovered (`No data` → Minnesota HQ + Ireland facility; candidates 5 → 19).
- **Test fixture fix** (commit e1ad1f9): `test_aggregate_list_column_no_conflict` used near-identical `"claim {i}"` strings that collided once `_DEDUP_RATIO` dropped 95→85 (2026-06-29). Triaged as a fixture artefact, not a product bug; fixture now uses distinct-topic strings.
- Known issue recorded (fixed 2026-07-02): 30-link cap applied pre-scoring in DOM order.
- 25-company ADLM validation sample added; clean-homepage comparison run v2.

## 2026-06-30
- ADLM exhibitor directory scraper (`adlm_scraper.py`): 716 exhibitors, 182/182 matched, 181 directory-sourced URLs + 1 manual. Two silent-corruption bugs caught by post-run audit (false-100 fuzzy matches from suffix stripping; footer social-link leak).
- Company-URL resolver (`src/resolve/`) demoted to fallback behind the directory scrape; direct internet search removed (Firecrawl-only).

## 2026-06-29 — v1.0-plant-milk-eval
- Plant-milk evaluation cycle closed and tagged. Final: F1 0.88 (R 0.91 / P 0.88), hallucination rate 0, against 141-claim analyst ground truth v3.
- Aggregate: `_DEDUP_RATIO` 95→85; Matrix reads aggregated values; set-union for list columns.
- Verify: markdown/whitespace normalisation before fuzzy compare + soft anchor threshold for long quotes.
- ⚠️ These metrics are locked for the dissertation — do not change scoring behaviour without re-running the Stage 10 evaluation.

## Earlier (see brain/decision-log.md)
- 2026-06-24: agentic keep/reject verification rejected; dual precision (strict + distinct) adopted.
- 2026-06-23: conflict detection gated on question type; null-sentinel handling unified.
- 2026-06-22: aggregate layer wired into Matrix (was dead code); filter passthrough mode added; evaluation aligner (greedy 1:1 + quote_id exception).
- 2026-06-20/21: chunked extraction (replacing 7k-char truncation); prompt hardened to verbatim single-sentence quotes; filter chunk-level max scoring, threshold 0.55.
- 2026-06-18: Firecrawl chosen as default fetcher (five-backend comparison); Playwright wait strategy fixed.
- 2026-06-15/17: layer separation (SGAI combined call rejected — Verify architecturally requires cached source); config-driven tool dispatch; per-question max crawl scoring; Ollama embeddings after HuggingFace block.
