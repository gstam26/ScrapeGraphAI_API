# Changelog

Notable changes, newest first. Full rationale for each decision: `brain/decision-log.md`.

## 2026-07-13/14

### Hybrid fetch backend promoted to production default (leadership sign-off 2026-07-13)
- `FETCH_BACKEND = "playwright_pooled_hybrid"` — Firecrawl credits cover only ~74/178 ADLM companies; the hybrid fetches free from our IP with politeness by construction. Firecrawl remains available per-workbook and as the proposed vendor fallback for denied sites.
- **Stage-2 parity measured** (the never-before-run bar item 1): replay of the pinned 25-entity page set through hybrid + `diagnostics/matrix_parity.py` (new tool: populated-cell retention vs a pinned baseline; warns when extract tools differ between runs). Result: Company type 100%, Diagnostics type 92% (bar 0.95) — losses = Agilent + Aladdin.
- **Attribution run separated fetch from model**: cache-served replay (Firecrawl text, new extractor) scored 100% on all four questions → the extractor change is blameless; every lost cell is fetch-side. Live probes then diagnosed each: Agilent = hard Akamai 403 (only a vendor can fetch it); Nova = Cloudflare beaten by our render; Neogen = our own escalation bug (fixed below); Aniara = gate over-flag (content still reaches extraction); Monobind = commerce template with no extractable text.
- **Hybrid escalation fix**: the static→render escalation shipped the render unconditionally; SPA pages that wipe server-rendered HTML on hydration made the render far thinner than the static extraction (Neogen: 20,346 chars static → 1,560 rendered → pipeline got the 1,560). Escalation now keeps whichever extraction is richer. Verified live.
- **Static-probe timeout cap** (`HYBRID_STATIC_TIMEOUT_S=12`): the hybrid's static attempt is a fast-path check, not a full fetch — a hanging response now escalates in 12 s instead of burning the full 30 s (FUJIFILM: 52 s → 7 s). A trialled networkidle render wait was **disconfirmed** (link-grid pages are genuinely thin when rendered; longer waits recover nothing) and reverted — negative result documented in `config.py`.
- Acquire Log now carries fetch provenance columns (Backend / Render Fallback / Gate Passed / Gate Reason) — recorded by the crawler since the pooled backends landed but previously dropped by the sheet writer.
- Proposal: `brain/proposals/vendor-fallback.md` — condition-triggered Firecrawl escalation for protocol-level denials (401/403/429/503 + failed render). The exception list is an output of each run (Acquire Log pivot), never a hardcoded site list. Awaiting review + leadership's spend approval.

### Extraction switched to Azure-direct GPT-4.1-mini
- The Power Automate proxy no longer serves GPT-5.5 (credit burn) — it now runs the same GPT-4.1-mini as the Azure-direct deployment, so the extra flow dependency buys nothing. `build_182_workbook.py` and the committed 182 workbook now set `EXTRACT_TOOL=azure`. Attribution run above doubles as the extractor's population-parity validation (100%).
- Caveat recorded: the 07-06b replay baseline is GPT-5.5-extracted and no new run can match it — future diffs against it conflate extractor and whatever else changed.

### CMO case study (real advisory input, second task)
- `scripts/build_cmo_workbook.py`: converts the client sheet (dirty real input: duplicate company rows, URLs with embedded newlines, scheme-less URLs, dead deep links) into the pipeline schema; `--check` probes every URL and writes a cohort inventory (measured: 91 unique entities, 29 usable seeds, 38 missing, 24 broken). `--entities` fixes a named sample across runs; `--max-pages` writes a `CRAWL_MAX_PAGES` override. Client data stays untracked.
- `scripts/run_cmo_depth_sweep.py` + `scripts/plot_cmo_depth_sweep.py`: one-command depth sweep (isolated per-depth caches, Excel-lock resilient, merging summary CSV) and presentation-ready plots (saturation curve, per-question heatmap, per-entity coverage).
- **Findings so far**: depth 2 adds ~1 cell over depth 1 at equal budget — the win was page budget, not depth; per-entity coverage exposed dead seeds (2 of 5 sample entities) as the real ceiling; the depth-1 no-instructions baseline is the pre-registered number the instructions run must beat.
- Fixed en route: depth-sweep cache contamination (per-run `CACHE_DIR` override in `_build_config`); `_parse_depth`'s hardcoded `{0,1,2}` whitelist (first depth-3 run ever attempted crashed on a validator assumption); `.gitignore` missing `cache_*/` variants (706 untracked cache blobs in VS Code).

## 2026-07-10

### Consent-overlay strip (bake-off finding: CMP dialogs extracted as page content)
- Hybrid bake-off run #1 (89 pages, 8 entities) surfaced identical char counts across different URLs — 4 Bruker pages all exactly 2,539 chars (OneTrust modal), 5 Hologic pages 651 chars (TrustArc) that **passed the quality gate**, i.e. cookie-policy text flowing to extraction as content. Root cause: the CMP dialog is the most paragraph-like block in the DOM, so Trafilatura extracts it instead of the page. Verified live on Bruker/Hologic/Metrohm: stripping the CMP container recovers real content (Hologic /cytology: consent text → "ThinPrep Pap test…").
- Fix: `_strip_consent_overlays()` removes known CMP vendor containers (OneTrust, Cookiebot, Usercentrics, Didomi, Quantcast, TrustArc, Osano, CookieYes, Complianz, Borlabs, iubenda — vendor container IDs only, no site-specific rules; substring fast-path skips the parse when no CMP present). Applied wherever HTML feeds extraction/gating: playwright_pooled, both hybrid paths, both local paths, the one-shot Playwright fallback. Firecrawl's markdown path deliberately untouched (locked plant-milk benchmark).
- Consequence: remaining gate failures are now honest (nav-heavy pages fail for being nav pages — their links still feed discovery). Bake-off stage 1 should be re-run; the identical-char clusters should disappear and gate-passed pages should contain real content.
- Suite: 174 offline tests green (4 new).

### Hybrid self-hosted fetch backend (`ACQUIRE_TOOL=playwright_pooled_hybrid`)
- Static-first variant of `playwright_pooled`: httpx GET → Trafilatura → full quality gate; escalates to the pooled browser render only when the static attempt fails the gate or errors. Pages that don't need JavaScript skip the browser entirely (and its 2s settle wait).
- Politeness identical to `playwright_pooled` by construction — the static path reuses the same `robots_allows` + `wait_for_domain_slot` primitives; the render escalation takes its own domain slot (correct: it's a second request). Closes the exact gap that ruled the old `local` backend out for external use.
- Provenance records the serving path per page: `pooled_hybrid_static` / `pooled_hybrid_render` (render_fallback=True). Render failure after a gate-failed static fetch keeps the static content with the failure recorded (`local`'s contract).
- Crawler link discovery treats the hybrid's HTML like `playwright_pooled`'s (full DOM either way).
- `backend_compare.py` gains `--backend playwright_pooled_hybrid` + a per-page "PW Backend" column + static-hit-rate summary, so one laptop bake-off measures both cell-population parity (bar item 1, never yet measured) and the hybrid's efficiency claim.
- ⚠️ Off by default; same leadership sign-off + bake-off go/no-go as `playwright_pooled` (brain/proposals/firecrawl-replacement.md). Note for the bake-off writeup: the static path has a different network fingerprint (plain httpx vs Chromium), so WAF behaviour (e.g. Agilent/Akamai) may shift vs the 07-05 run — a second moving variable alongside the robots-UA fix.
- Suite: 170 offline tests green (6 new).

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
