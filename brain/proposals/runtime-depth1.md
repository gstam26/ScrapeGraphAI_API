# Proposal: Depth-1 runtime — where the time goes and what to do

**Status:** Investigation complete (2026-07-02, from code + the 25-company validation run log). No code changed.
**Verdict:** No new instrumentation needed. The bottleneck is unambiguous from the existing logs: **serial page fetching inside a serial entity loop**. Wall clock is fixable by ~4–6× with parallelism alone, plus a further cut from not fetching worthless pages.

## What I found

### Time attribution (validation run, 25 companies, depth 1, 36m 44s total)

Summing the per-entity phase times printed by `pipeline.py`:

| Phase | Total | Share | Per entity (typical) |
|---|---|---|---|
| Acquire | ~28 min | **~75%** | 43s–2m14s (8 entities >80s) |
| Filter+Extract+Verify | ~9.5 min | ~25% | 12–42s |

Acquire is ~4–6 s/page × ~15 pages, **fully serial**, twice over:

1. **Pages within an entity are fetched one at a time** — `crawl_entity` is a single `while queue` loop (`src/acquire/crawler.py:274-406`); each iteration blocks on one Firecrawl round-trip (`_acquire_page_cfg` → `fetch_page_with_provenance`).
2. **Entities are processed one at a time** — `run_pipeline` iterates `for spec in request.urls` (`pipeline.py:160`) with no concurrency. The `ThreadPoolExecutor` at `pipeline.py:222` parallelises Filter/Extract/Verify *within* one entity (4 page workers × 8 chunk workers) — which is why Extract is already only 25% — but Acquire never overlaps anything.

**182-run projection at current shape:** mean ~88 s/entity × 182 ≈ **4h 27m**, three-quarters of it Firecrawl latency we're waiting on serially.

### Secondary finding: a large fraction of fetched pages are worthless

From the same log: Bruker spent 9 of its 15-page budget on locale homepages (`/fr`, `/ko`, `/de`, `/pl`, `/es`, `/pt`, `/ru`, `/zh`, `/it`); Metrohm ~10 (`th_th`, `ko_kr`, `zh_tw`, `en_be`, …); QuidelOrtho ~12 country variants; Thermo Fisher spent budget on `/auth/initiate` and search-category pages. These pages score ~0.55-0.63 (they contain the same nav text as the real homepage), duplicate each other's content, and produce duplicate or empty cells. This is both a **speed** cost (each is a 4–6 s fetch + LLM calls) and a **recall** cost (they crowd out About/locations/news pages — directly implicated in the Q1 R&D-location starvation seen in the run). Related recorded issue: pre-scoring 30-link cap (`decision-log 2026-07-01`, `crawler.py:162,196`).

### Constraint check (politeness)

The crawl path has **no rate limiting, no delays, no robots.txt handling** (grep: only `sleep` in the repo is the SGAI retry, `src/extract.py:173`). Today this is masked because Firecrawl fetches from *its* infrastructure. Any parallelism design must keep per-domain request rates bounded — see the interaction with the Firecrawl-replacement proposal.

## Recommendations (in order of value/effort)

### R1 — Parallelise across entities (biggest win, low risk)
Wrap the `for spec in request.urls` loop body in a `ThreadPoolExecutor` (4–6 workers). Each entity is a different domain, so **per-domain traffic is unchanged** — politeness is preserved by construction. Expected: 4h27m → **~50–70 min** for the 182.

Implementation notes (for when the freeze lifts):
- The crawler writes into the shared `diag` dict mid-crawl and `pipeline.py:114-127` annotates rows by list-index ranges (`acquire_start` slicing) — this breaks under concurrency. Fix: per-entity local diag (the pattern already used in `process_page`, `pipeline.py:202-218`), merged after each future completes.
- Throughput ceilings to respect: Firecrawl plan concurrency (check the standard-tier limit before picking worker count) and the Power Automate LLMAPI (one 502 already seen in the validation run at current load — see R4).
- `_question_emb_cache` in `src/filter.py:29` is a plain dict mutated from threads; benign in CPython but worth a lock for cleanliness.

### R2 — Stop fetching worthless pages (speed + recall, medium effort)
Two generic (non-domain-specific) filters in link hygiene, next to `_is_junk_link` (`crawler.py:96`):
- **Locale-variant suppression:** drop candidate URLs whose path is only a locale code (`/fr`, `/de.html`, `/us_en/...`, `/ko_kr.html`) when a same-page non-locale variant is already queued/visited. Pattern-based (`^[a-z]{2}([_-][a-z]{2})?$` path segments), not a site list.
- **Score-aware cap:** the already-recorded fix — score all discovered candidates, keep top-N by score instead of first-N in DOM order.

Effect: fewer pages/entity (faster) and budget freed for About/locations/news (Q1/Q4 recall). Must be validated on the 25-sample before the 182 (same before/after discipline as the rawHtml fix).

### R3 — Modest within-entity fetch concurrency (optional, after R1)
Fetch same-depth queued links 2–3 at a time. Smaller marginal win once R1 lands, and it *does* raise per-domain rate — acceptable while Firecrawl proxies; revisit if a self-hosted fetcher lands. Do R1 first and measure; skip R3 if the 182 is already <1 h.

### R4 — LLMAPI 5xx retry (robustness, tiny)
`_extract_with_llmapi` (`src/extract.py:256-268`) treats a 502 as a permanent empty result. One 502 occurred in 25 entities (Sebia); at 182 × ~15 pages expect several — each silently blanks 4 cells for that chunk. One retry with short backoff on 5xx is cheap and makes runs reproducible. (Do NOT retry on timeout — that's already handled.)

### Explicitly not recommended
- Optimising the Ollama link scorer — it's batched (one `embed_batch` per page, `link_scorer.py:140`) and a rounding error next to fetch latency.
- New instrumentation as a prerequisite — `Acquire Log.fetch_time_ms` per page plus the phase prints already attribute the time fully. (Nice-to-have later: a per-entity link-scoring-time column to prove the above.)

## Decision points
- **George:** entity-parallelism worker count (needs the Firecrawl plan's concurrency limit); accept generic locale-suppression heuristic (R2) as non-domain-specific.
- **Nick:** none for R1/R4. R2/R3 only if bundled with the self-hosted-fetch decision (IP exposure — see `firecrawl-replacement.md`).
