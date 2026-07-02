# Proposal: Replacing Firecrawl with a self-hosted fetch backend

**Status update (2026-07-02, evening):** the backend is now **BUILT** — `ACQUIRE_TOOL="playwright_pooled"` (`src/acquire/playwright_pool.py` + fetcher/crawler wiring), off by default, with the politeness gate included (per-domain ≥2 s delay via `CRAWL_POLITE_DELAY_S`, robots.txt respected via `CRAWL_RESPECT_ROBOTS`, honest UA). Offline-tested (8 tests). **Not yet used against external sites** — that still needs Nick's sign-off, now urgent because remaining Firecrawl credits (1,025) cover only ~74 of 178 companies. Explicitly out of scope on request-but-declined grounds: free proxies (security hazard — third parties MITM Sagentia traffic) and stealth anti-bot evasion (hard-blocked sites are recorded as findings instead). The bake-off below is unchanged and is the go/no-go.

**Original investigation (2026-07-02, morning):** feasible and worth testing — but the blocker is not technical. The decisive question is IP exposure/politeness policy, which is Nick's call.

## What the interface actually requires

A fetch backend must satisfy two contracts (`src/acquire/fetcher.py`):

1. **Content:** `(text, html_or_None, FetchProvenance)` from `fetch_page_with_provenance` — markdown-ish text for Extract/Verify, provenance fields for the logs.
2. **Link discovery:** raw rendered HTML. This is the subtle one — the 2026-07-01 fix exists precisely because Firecrawl's markdown *and* cleaned HTML drop nav/footer links; only `rawHtml` keeps them (`_fetch_firecrawl_doc`, `fetcher.py:132-146`; `_discover_links`, `crawler.py:212-213`).

**A Playwright-based backend satisfies both natively.** `page.content()` after render *is* the raw DOM — the nav-link-loss problem that forced the rawHtml fix cannot occur. Text extraction via the existing Trafilatura path (`_extract_text_from_html`, `fetcher.py:35`). All pieces already exist in the repo: `_fetch_local` (httpx + Trafilatura + quality gate + Playwright fallback, `fetcher.py:202-244`) and `_render_page_html` (`fetcher.py:184-195`). `requirements.txt` already ships playwright + trafilatura. No new dependencies, no IT asks for software.

## What Firecrawl provides that is hard to replicate

| Capability | Replicable? | Notes |
|---|---|---|
| JS rendering | Yes — Playwright | Proven: 60/60 pages, 12k avg chars in the five-backend comparison (decision-log 2026-06-18) |
| Markdown conversion | Yes — Trafilatura | Local backend already does it |
| Raw HTML for link discovery | Yes, **better** | See above — no rawHtml workaround needed |
| Speed per page | Partially | Firecrawl ~4–6 s/page (validation run); repo's Playwright ~5–17 s because `_render_page_html` launches a **fresh Chromium per page**. A persistent browser + page pool removes ~1–2 s launch overhead per page; domcontentloaded+2s is then ~3–5 s/page — comparable |
| **IP reputation / proxying** | **No** | Firecrawl fetches from *its* IPs. Self-hosting means every request comes from Sagentia's network — the company has had IPs blocked before, and the crawl path currently has **zero rate limiting or robots.txt handling** (verified by grep; only `sleep` in repo is an SGAI retry). This is the real gap. |
| Anti-bot handling (Cloudflare etc.) | Partially | Headless Chromium passes most; some sites will block. Firecrawl failed 5/60 on plant-milk too — neither is perfect. Measure, don't assume. |
| PDF → markdown | No (not built) | Crawler already skips `.pdf` links (`_JUNK_EXTS`, `crawler.py:90`), so nothing currently depends on it. Note as a lost future option. |

Also on the ledger: the validation run showed Firecrawl itself failing **on the Sagentia network** — 3 EUROIMMUN pages lost to `SSL: CERTIFICATE_VERIFY_FAILED (self-signed certificate in chain)`, i.e. corporate TLS interception of the api.firecrawl.dev call. A local backend doesn't call out to a third-party API at all, removing that failure mode (its own fetches go through the same network but direct to target sites).

## Proposed test: a measured bake-off, not a leap of faith

**Harness:** extend the existing five-backend methodology (decision-log 2026-06-18) / `diagnostics/acquire_batch_eval.py`. Two configs, identical everywhere else (the plug-in dispatch exists for exactly this — decision-log 2026-06-15):
- A: `ACQUIRE_TOOL="firecrawl"` (current)
- B: `ACQUIRE_TOOL="playwright_pooled"` (new: persistent browser, Trafilatura text, raw DOM for discovery, quality gate from `_fetch_local`)

**Corpus:** the 25-company ADLM validation sample (primary — real target material, fresh fetches with cache cleared), plus the 10 plant-milk sites (secondary — known-hard JS cases: Silk/React was the original Playwright stress test).

**Metrics (per backend, per site):**
1. Fetch success rate (non-empty, gate-passed pages)
2. Content volume (chars) and — more meaningful — **downstream populated-cell count per question** after an identical Extract pass
3. Link-discovery quality: candidate count and whether About/Contact/locations/news links are discovered (the Surmodics test, decision-log 2026-07-01)
4. Wall time per page and per entity
5. Failure taxonomy: blocked (403/captcha), timeout, thin content

**"Good enough" bar (pre-registered so we can't rationalise after):**
- ≥ 95% of Firecrawl's populated cells on Q2/Q3 (companies/diagnostics — the easy, high-volume questions)
- No regression on Q1/Q4 discovery-link coverage (expect improvement, given raw DOM)
- ≤ 1.5× Firecrawl wall time per entity **after** entity-level parallelism (see `runtime-depth1.md` — parallelism matters more than per-page speed)
- Zero-page total failures ≤ Firecrawl's on the same corpus
- No target-site complaints/blocks during the test (see politeness gate below)

**Politeness gate (blocking prerequisite):** before any self-hosted crawl of external sites at scale, the backend needs (a) per-domain rate limit (e.g. ≥2 s between requests to one domain — cheap to add since entity-parallelism naturally puts one domain per worker), (b) robots.txt respect (stdlib `urllib.robotparser`), (c) an honest User-Agent (config already has one, `config.py:71`). This is required by the stated Sagentia constraint and is currently absent from the code.

## Recommendation

Build the pooled-Playwright backend behind the existing dispatch (one new `_FETCHERS` entry + pool management, no changes to Filter/Extract/Verify), add the politeness gate, run the bake-off on the 25-sample. Decide on the pre-registered bar. Keep Firecrawl config intact regardless — swappable fetchers are the architecture's point, and "local backend passed a measured bake-off against the commercial default" is strong RQ2/generalisability evidence for the dissertation either way.

**Adoption upside beyond the $83/mo:** removes a subscription + API-key dependency from the Advisory team's long-term operation of the tool — one fewer procurement/renewal barrier.

## Decision points
- **Nick (blocking):** authorise self-hosted crawling from the Sagentia network given the IP-blocking history — with the politeness gate above as the mitigation; confirm acceptable per-domain rate; sign off Firecrawl cancellation criteria (the pre-registered bar).
- **George:** whether the bake-off runs before or after the 182 production run (recommend after — don't destabilise the fetch layer mid-campaign; the 182 also generates free Firecrawl-side baseline data for comparison).
