# Brain / Docs Audit — 2026-07-02

Cross-check of every factual claim in `brain/` (and root docs) against live code.
**Code is the truth.** Each finding: claim → file → what the code actually says.

## A. Divergences in brain files (to fix in brain)

### A1. Extract "primary" tool — tool-register.md
- **Claim:** "GPT-5.5 via Power Automate (LLMAPI) — Extract (primary), Active".
- **Code:** `config.py:28` — `EXTRACT_TOOL = os.getenv("EXTRACT_TOOL", "azure")`. The repo default is **azure** (gpt-4.1-mini). LLMAPI is selected *per run* via the workbook `config` sheet (`build_182_workbook.py:56` sets `EXTRACT_TOOL=llmapi`) or via `.env`.
- **Fix:** reword to "primary for production/ADLM runs via workbook or .env override; config.py default is azure".

### A2. Missing Claude extractor row — tool-register.md
- **Claim:** register has no Claude row; report-deltas says direct Claude API is blocked by Sagentia IT.
- **Code:** `src/extract.py:350` `_extract_with_claude` calls `https://api.anthropic.com/v1/messages` directly (`CLAUDE_MODEL` default `claude-haiku-4-5-20251001`, `config.py:17`). Dispatched via `EXTRACT_TOOL="claude"`; documented in README as an option for small/spot-check runs.
- **Fix:** add row. Note the constraint: works off-network only (direct LLM APIs blocked on Sagentia network), so not usable for on-network production runs — the report-deltas claim stays true *for the Sagentia environment*, but the code path exists.

### A3. nomic-embed-text layer coverage — tool-register.md
- **Claim:** Layer = "Acquire + Filter".
- **Code:** `src/verify.py:98-137` `verify_cells` also embeds claim↔quote pairs to compute `semantic_score` (diagnostic, not a gate).
- **Fix:** Layer = "Acquire + Filter + Verify (diagnostic score)".

### A4. Dispatch function names — decision-log 2026-06-15 (plug-in dispatch)
- **Claim:** "Dispatch functions (`_get_fetcher`, `_get_extractor`) route to the appropriate implementation."
- **Code:** no such functions. Fetch dispatch is the `_FETCHERS` dict + special-cases in `fetch_page_with_provenance` (`src/acquire/fetcher.py:247-307`); extract dispatch is an if/elif chain in `extract_cells` (`src/extract.py:658-668`).
- **Fix:** correct the mechanism description (architectural point stands).

### A5. SGAI fetcher "Dropped" — tool-register.md
- **Claim:** "Dropped … Do NOT use as fetcher."
- **Code:** `sgai` is still a valid, selectable backend (`_FETCHERS`, `src/acquire/fetcher.py:247`). Dropped as a *recommendation*, not removed from dispatch.
- **Fix:** clarify "still selectable in dispatch; do not choose it".

### A6. Filter mode for the 182 run — task framing vs config
- **Claim (task context / pending-items):** "filter set to passthrough pending threshold recalibration".
- **Code:** `config.py:155` `FILTER_MODE = "threshold"` right now, and **FILTER_MODE is not a workbook-overridable key** (`_SUPPORTED_CONFIG_KEYS`, `src/io_excel.py:31-39`; noted in `build_sample_workbook.py:64`). Passthrough runs require a manual `config.py` edit (the 2026-07-01 passthrough sample was run that way).
- **Fix:** record explicitly in `brain/tasks/adlm-2026.md`: passthrough for the 182 run = manual config.py edit before launch; it is not baked in.

### A7. BM25 fallback wording — decision-log 2026-06-15 (Ollama)
- **Claim:** "The BM25 fallback ensures the pipeline continues to function when Ollama is unreachable."
- **Code:** BM25 fallback exists only in the **crawler** (`src/acquire/crawler.py:380-383`). The Filter's fallback is route-all-columns (`src/filter.py:182-184`), and Verify's is a skipped semantic score. tool-register.md states this correctly; the decision-log sentence over-generalises.
- **Fix:** qualify the sentence.

## B. Claims verified accurate (no change needed)

- Firecrawl rawHtml link-discovery fix: `_fetch_firecrawl_doc` requests `["markdown","rawHtml"]`, `_discover_links` prefers HTML when `acquire_tool == "firecrawl"` (`fetcher.py:132-146`, `crawler.py:212-213`); 4 smoke tests monkeypatch `_fetch_firecrawl_doc` (`tests/test_smoke.py:698-738`). Commit 322d0ec exists.
- Fixture fix landed: `test_aggregate_list_column_no_conflict` now uses distinct-topic strings (`tests/test_smoke.py:556-561`), commit e1ad1f9. Decision-log status "FIXED" is correct.
- Link-cap-before-scoring issue: confirmed — `candidates[:CRAWL_MAX_LINKS_PER_PAGE]` applied at the end of both discovery functions, before any scorer (`crawler.py:162,196`). Still unfixed, as recorded.
- `_DEDUP_RATIO = 85` (`src/aggregate.py:11`); `_UNION_LIST_COLS = {"Plant milk types"}`; null-sentinel handling; list-column conflict gating incl. the `\bone\b.{1,30}\bper\b` regex — all match the log.
- Verify Option A + C: `_norm` + `partial_ratio ≥ 70`, soft gate ≥ 68 for quotes ≥ 100 chars with both 20-char anchors literal (`src/verify.py:24-45`; `config.py:171-173`).
- Filter: chunk-level max-cosine, 100-chunk cap, keyword OR-gate, threshold 0.55, passthrough mode still logs scores — all match `src/filter.py`.
- Crawler: per-question max cosine, entities excluded, page-type multiplicative adjustment `topic * (1 + 0.4 * type_score)` (`link_scorer.py:153-158`), experimental scorer opt-in with double fallback.
- Playwright: `domcontentloaded` + 2 s wait, 15 s timeout (`fetcher.py:191-192`).
- Matrix reads aggregated `row.cells`, Summary/Provenance read raw `row.all_cells` (`io_excel.py:303,346,425`).
- report-deltas §5.5: `char_span` computed (`verify.py:71`) but still **not** written to Provenance (`io_excel.py:413-415` — no Char Span column). Claim remains true. (Provenance has since gained a Semantic Score column — an addition, not a contradiction.)
- Resolver: search-only default, `--fetch` opt-in (`resolve_urls.py:50`). ADLM scraper findings match `adlm_scraper.py`.

## C. Not verifiable from code (left as recorded)

- Backend-comparison numbers (60-page runtimes/chars), eval metrics (F1 0.88 etc.), Firecrawl pricing (~$83/mo), crawl-scorer ~AUC-0.5, Surmodics cache-size observations. These are run artefacts, not code; treated as historical record.

## D. Stale root docs (fixed in Task 2)

- `PROJECT_STRUCTURE.md`: describes the pre-restructure tree — layers (`filter.py`, `extract.py`, `verify.py`, `aggregate.py`, `io_excel.py`) at root (now `src/`), tests at root (now `tests/`), `diagnostics/llmapi.py` (now `src/llmapi.py`), no `src/resolve/`, no `src/embed.py` mention in the "should move" analysis. Superseded; candidate for deletion or replacement by README + code-restructure proposal.
- `README.md`: "Extract — Ask ScrapeGraphAI…" (extractor is dispatch of sgai/llmapi/azure/claude); "Run unit smoke tests: `python test_smoke.py`" (now `tests/`); Project Structure section stale; no mention of Sagentia network constraints, Ollama dependency, or the local backend in the acquire list.

## E. Stale code comments (code is frozen this session — recorded only, do NOT edit)

- `config.py:158-159`: comment under `FILTER_THRESHOLD = 0.55` still explains the old 0.35 value.
- `src/aggregate.py:9-10` vs `:159`: header comment says `_DEDUP_RATIO` is "intentionally lower than AI_DEDUP_RATIO (95)" while the inline comment at 159 says it "uses the same threshold as the eval's AI_DEDUP_RATIO". One of the two is stale (eval-side value needs checking against `diagnostics/eval_lib/metrics.py` when unfrozen).
- `config.py:23-24`: `FETCH_BACKEND` comment says "Dev default. Change to 'firecrawl' for deployment" but the value already *is* "firecrawl".

## F. Dead / orphaned candidates (list only — nothing deleted)

| Item | Evidence |
|---|---|
| `config.py`: `CRAWL_ENABLED`, `FETCH_WAIT_MS`, `ENABLE_COST_TRACKING`, `ENABLE_LATENCY_TRACKING`, `ENABLE_PROVENANCE` | defined, never read anywhere (`Config.fetch_wait_ms` model field also unused by fetchers) |
| `models.py`: `EvidenceItem`, `CellContribution` | alias classes, zero references outside models.py |
| `PROJECT_STRUCTURE.md` | stale snapshot (see D) |
| `diagnostics/crawl_trace.py`, `crawl_trace_embed.py`, `crawl_collect.py`, `crawl_debug.py` | already flagged archive/removal candidates in `diagnostics/DIAGNOSTICS_INVENTORY.md` |
| `diagnostics/fetch_eval/<timestamps>/` | archived run artefacts (gitignored) |
| `matched_official_urls.csv`, `adlm_exhibitors_full.csv` | **load-bearing but unversioned** — gitignored by the `*.csv` blanket rule, yet `build_182_workbook.py` reads `matched_official_urls.csv`. Risk: 182-run input not reproducible from the repo. Consider `!matched_official_urls.csv` exception. |
| `build_182_workbook.py` | untracked (new; intended for the 182 run) |
| root `__pycache__/` | ignored, cosmetic |
