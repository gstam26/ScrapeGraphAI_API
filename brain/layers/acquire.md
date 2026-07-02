# Acquire

**Responsibility:** get the right pages. Completeness lives HERE (Filter may only route, never exclude — 2026-06-15). Seed fetch + guided same-domain crawl, disk cache, fetch provenance.

## Interface
- In: `acquire(urls: [(url, depth)], cfg: Config, columns, entities, diag)` — `src/acquire/__init__.py`
- Out: `list[FetchedPage]` — url, markdown text, status (ok/cached/gate_failed/error), depth, crawl_score, fetch_time_ms, backend, render_fallback, gate_passed/reason
- Cache: `cache/<sha256(url)>.txt|.md` (`src/acquire/cache.py`); text only, HTML not cached

## Current implementation
- Backend dispatch `_FETCHERS` (`fetcher.py`): firecrawl (default) / local / playwright / requests / sgai (dropped). Firecrawl requests `["markdown","rawHtml"]`; raw HTML feeds link discovery because Firecrawl's markdown AND cleaned html drop some nav/footer links (2026-07-01, validated on Surmodics).
- Thin-content (<200 chars) → one Playwright re-render (`THIN_CONTENT_FALLBACK`). Local backend: 3-rule quality gate (min chars / link density / content ratio) → Playwright on failure.
- Crawler (`crawler.py`): BFS, depth ≤ 2, ≤ `CRAWL_MAX_PAGES=15` pages/entity. Link scoring: per-question max cosine (entities excluded), page-type adjustment `topic × (1 + 0.4·(info−trans))`, threshold 0.50; BM25 fallback. Top-2 fallback when nothing clears threshold at depth ≤ 1.
- Link hygiene: same-domain, junk extensions, **locale-variant dedup** (`_locale_key`, 2026-07-02), **score-aware top-30 cap** (post-scoring, 2026-07-02).

## Known issues
- **No rate limiting / robots.txt / politeness anywhere** — masked today by Firecrawl proxying. Blocking prerequisite for any self-hosted backend (`proposals/firecrawl-replacement.md`).
- Crawl/filter embedding scorer measured ~AUC-0.5 on the ADLM task — locale dedup + score-aware cap improve the candidate pool, not the scorer.
- Q1 (R&D location) starvation persists after the rawHtml fix + passthrough (validation run 2026-07-02).
- Firecrawl API calls fail under corporate TLS interception (SSL self-signed-chain errors, seen on-network).
- Bot-gated sites (Tosoh-class: static 403, JS nav) untested.
- Pages within one entity fetched serially (entity-level parallelism added 2026-07-02; within-entity concurrency deliberately not — per-domain politeness).

## Open questions
- Self-hosted pooled-Playwright backend — blocked on Nick (IP exposure). See proposal.
- Systematic depth 0/1/2 completeness experiment (report §5.3) still outstanding.
