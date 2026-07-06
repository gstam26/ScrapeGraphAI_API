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

---

## Results (2026-07-05): 25-entity bake-off, extended from the 8-entity quality mix — FAILS the bar, not yet ready to flip the default

**Machine and its limits, verified this session (not assumed):** this is George's personal Dell, not the Sagentia work laptop. `curl -m5 http://10.99.96.1:11434` (Ollama, needed by Filter/Group) times out (`curl` exit 28, HTTP code 000) — confirmed again this session, same result as 2026-07-02. Google/public internet reachable fine; egress IP is `82.13.63.41`, **not a Sagentia IP** — this run's "no blocks observed" result does not validate the real-world IP-reputation scenario Nick is being asked to sign off on. `AZURE_ENDPOINT` (`https://thebeastgpu.openai.azure.com/openai/v1`) is network-reachable (HTTP 404 on a bare GET, i.e. TCP/TLS reaches it) but there is **no `.env` file on this machine at all** — `AZURE_API_KEY`, `SGAI_API_KEY`, `CLAUDE_API_KEY`, `FIRECRAWL_API_KEY` are all unset. So Extract is blocked by two independent, compounding reasons here: no VPN path to the internal LLMAPI proxy, and no credentials for any of the public LLM backends either.

**One real end-to-end attempt** (not a guess): ran the actual pipeline (`pipeline.run_pipeline`) on 1 company (Catachem) with `ACQUIRE_TOOL=playwright_pooled`, `EXTRACT_TOOL=llmapi` (the config the 07-03 baseline used). It does **not hang** — it fails soft at every internal-network-dependent stage and completes in ~21s:
  - Acquire: real fetch succeeded, 17.8s, 1 page.
  - Filter: `embedding failed (<urlopen error [WinError 10060] ...>); routing all columns` — Ollama timeout, correctly fails open (no filtering, not a crash).
  - Extract: `LLMAPI extraction error: LLM_API_URL not set in .env` — 4/4 cells returned `(no data extracted)`.
  This confirms the memory's prediction with an actual error message rather than an assumption, and shows the failure mode is graceful (empty cells), not a hang.

**Harness:** `diagnostics/backend_compare.py`, unmodified (still defaults to the original 8-entity mix; extended via its existing `--entities` flag, no script changes), run against `--baseline adlm-outputs/validation_sample_run_2026-07-03.xlsx` covering **all 25 entities / 345 pages** from that run's Acquire Log. Output: `adlm-outputs/backend_compare_25_playwright_vs_firecrawl_2026-07-05.xlsx`; raw console log: `adlm-outputs/backend_compare_25_run.log`. Politeness gate verified live in the code path (not just present in config) before running: `_fetch_playwright_pooled` (`src/acquire/fetcher.py:184`) calls `fetch_rendered_html` (`src/acquire/playwright_pool.py:160`), which calls `robots_allows()` then `wait_for_domain_slot()` before every fetch — both gate the actual request, not dead config. `CRAWL_POLITE_DELAY_S=2.0` and `CRAWL_RESPECT_ROBOTS=True` are the config values read (`config.py:59,62`).

**Headline number:** 169/345 pages OK (49.0%), 174 gate_failed, 2 timeout errors. Median chars ratio 0.13 (pw vs fc; expected to run well below 1.0 per the script's own docstring — Trafilatura strips nav/boilerplate Firecrawl markdown keeps — so this alone isn't a failure signal). Link discovery: 17,267 pw vs 653 fc same-domain links — the raw-DOM thesis (§ above) holds strongly even amid the failures. Overall summed fetch time: 903.7s pw vs 1,964.4s fc (**0.46×** — playwright_pooled is faster in aggregate, not slower).

**Root-cause taxonomy of the 174 gate_failed + 2 error pages (diagnosed by reading actual fetched content/HTML, not inferred from the status column alone):**

1. **Robots.txt-fetch User-Agent bug — 60/345 pages (17%), 4/25 entities at 0% (Aalto Scientific, Greiner Bio-One, Metrohm USA, Neogen — every one of their 15 pages `robots_disallowed`).** Root cause verified directly: `robots_allows()` (`src/acquire/playwright_pool.py:68-92`) calls stdlib `RobotFileParser.read()`, which fetches `/robots.txt` via `urllib.request` with **no custom headers** — sending Python's default `Python-urllib/x.y` UA. All 4 domains' WAFs return `HTTP 403` to that bare UA (`urllib.request.urlopen('https://www.metrohm.com/robots.txt')` → `HTTPError: 403`), and Python's `RobotFileParser` sets `disallow_all=True` on a 401/403 (its documented behaviour). But the exact same URL with the pipeline's own declared UA (`curl -A "Mozilla/5.0 entity-extraction-pipeline"`) returns **200**, and each robots.txt itself reads `User-agent: * / Allow: /` with no rule that would block these paths. So these 60 pages are **false-positive blocks caused by the code never sending its own honest UA on the robots.txt-fetch leg** — it sends it everywhere else (page fetch, per `context.new_page()` with `user_agent=_USER_AGENT`) but not here. This is a narrow, precisely located, plausibly one-line fix (pass a `Request` with the same UA header into `RobotFileParser`, or fetch robots.txt manually with `httpx`/`REQUEST_HEADERS` and feed it to `.parse()`), not a fundamental backend limitation. Not fixed in this session (out of scope per the brief — report, don't fix).
2. **Real anti-bot block observed — Agilent Technologies, 14/15 pages.** Direct inspection of the returned text: `"Reference #18.591e1202.1783259279.f9ee066\nhttps://errors.edgesuite.net/..."` — an Akamai edge error/challenge page, not Agilent's real content. This is a genuine block signal against this run's residential IP / headless-Chromium fingerprint, independent of the robots bug. **This is the one clear instance of bar item 5 failing** in this run.
3. **Unresolved SPA-hydration-timing issue reproduces on a new site (previously diagnosed 2026-07-02 on Bruker, never fixed in code — `settle_ms`/`page.wait_for_timeout` is still a flat 2000ms in both `playwright_pool.py:175` and `fetcher.py:221`).** Aladdin Scientific: 13 different product-page URLs all returned **the identical 333-char generic storefront shell text** and all passed the quality gate as `"ok"` — a false positive in the other direction: not a visible failure, but silently wrong (per-page) content, because the SPA's route-specific content hadn't hydrated within the fixed 2s settle window. Worth flagging as higher-risk than an outright failure because it would not show up as an error in the Matrix — it would show up as unexpectedly thin/wrong answers.
4. **Link-density quality-gate over-rejection — e.g. Aniara Diagnostica (11/15 pages), Aalto/Aladdin partly.** Spot-checked actual text on `link_density`-failed pages (e.g. `www.aniara.com` homepage): genuinely on-topic, substantive company content ("Aniara Diagnostica is the specialist North American distributor for hemostasis..."), rejected only because `QUALITY_MAX_LINK_DENSITY=0.60` is tripped by normal nav-heavy corporate/catalog page structure. A threshold-tuning issue, not a block or a bug.
5. **HORIBA — 2 real timeouts** (`Page.goto: Timeout 15000ms exceeded`), both slow-loading pages; a genuine slow-site timeout, not a block.

**Pre-registered bar — scored on this run's actual numbers:**

| # | Bar item | Verdict | Evidence |
|---|---|---|---|
| 1 | ≥95% of Firecrawl's populated cells on Q2/Q3 | **NOT MEASURABLE FROM THIS MACHINE** | Extract layer confirmed unreachable AND uncredentialed here (real 1-company attempt above); needs the work laptop. |
| 2 | No regression on Q1/Q4 discovery-link coverage | **PASS (proxy measure)** | 17,267 pw links vs 653 fc candidates, summed across all 25 entities, including links recovered from pages whose text-extraction failed the quality gate (matches production: `crawl_entity` calls `_discover_links` regardless of `gate_passed`, `src/acquire/crawler.py:404`). Not a perfect proxy for "cells populated," but real, strongly positive signal. |
| 3 | ≤1.5× Firecrawl wall time per entity (post entity-parallelism) | **PASS, provisionally** | Summed per-page fetch time 903.7s pw vs 1,964.4s fc = 0.46×, well inside the bar. Caveat: `backend_compare.py` is single-threaded (no `PIPELINE_ENTITY_WORKERS` parallelism applied on either side), so this is a fetch-time-only proxy, not a measured end-to-end production wall-clock; both sides are summed the same way so the ratio should be roughly parallelism-invariant, but this hasn't been verified under the real concurrent harness. |
| 4 | Zero-page total failures ≤ Firecrawl's | **FAIL, but root cause identified and narrow** | Firecrawl: 0/25 entities with zero pages fetched. Playwright_pooled: 4/25 (Aalto, Greiner, Metrohm, Neogen) — **all 4 attributable to the single robots.txt-fetch UA bug above**, not to genuine site blocks or a backend limitation (each site's own robots.txt allows the honest UA). If that one bug is fixed, this item plausibly flips to PASS on a re-run — but that's a prediction, not a re-measured fact. |
| 5 | No target-site complaints/blocks during the test | **FAIL (1/25), with the IP-origin caveat** | Agilent Technologies returned an Akamai edge-error page on 14/15 pages — a real block signal, observed from this run's residential IP (`82.13.63.41`), not a Sagentia IP. Whether Sagentia's IP reputation would fare better or worse against Agilent's Akamai WAF is **unknown and not answered by this run** — this is exactly the scenario the pre-registered caveat exists for. |

**Decision: do NOT flip `ACQUIRE_TOOL`'s default in `config.py`.** Item 1 (the central, cell-population bar) cannot be measured from this machine at all — no VPN, no `.env`. Item 4 fails as coded (though the cause is narrow and named). Item 5 has one real, unresolved block. Per the pre-registered discipline ("we can't rationalise after"), a partial pass on the measurable subset plus two unresolved bugs is not a pass. `brain/tool-register.md` and `brain/decision-log.md` are intentionally **not** touched by this session — no default flip to record.

**Full per-entity table (25/25, from the 2026-07-05 run):**

| Entity | Pages | PW ok | Gate-failed | Error | Med chars ratio | PW links | FC cands | PW time (s) | FC time (s) | Wall ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| Aalto Scientific Ltd.- Audit MicroControls | 15 | 0 | 15 | 0 | 0.00 | 0 | 16 | 0.3 | 119.0 | 0.00 |
| Acro Biotech Inc. | 13 | 12 | 1 | 0 | 0.23 | 171 | 12 | 29.8 | 112.4 | 0.27 |
| Agilent Technologies | 15 | 1 | 14 | 0 | 0.03 | 0 | 30 | 33.2 | 85.2 | 0.39 |
| Aladdin Scientific | 15 | 14 | 1 | 0 | 0.01 | 388 | 30 | 32.7 | 102.8 | 0.32 |
| Aniara Diagnostica | 15 | 1 | 14 | 0 | 0.44 | 2008 | 30 | 42.2 | 76.3 | 0.55 |
| Bruker | 15 | 13 | 2 | 0 | 0.23 | 500 | 25 | 34.9 | 59.0 | 0.59 |
| Calbiotech Inc. | 15 | 1 | 14 | 0 | 0.26 | 523 | 30 | 37.5 | 58.1 | 0.65 |
| Catachem | 1 | 1 | 0 | 0 | 0.57 | 1 | 0 | 3.5 | 5.2 | 0.67 |
| Danaher | 15 | 15 | 0 | 0 | 0.33 | 553 | 30 | 80.3 | 66.2 | 1.21 |
| EUROIMMUN | 15 | 6 | 9 | 0 | 0.07 | 768 | 30 | 58.6 | 74.8 | 0.78 |
| FUJIFILM Healthcare Americas Corporation | 1 | 1 | 0 | 0 | 0.51 | 1 | 0 | 7.1 | 7.0 | 1.01 |
| Greiner Bio-One North America Inc. | 15 | 0 | 15 | 0 | 0.00 | 0 | 30 | 0.1 | 63.3 | 0.00 |
| HORIBA | 15 | 6 | 7 | 2 | 0.11 | 2480 | 30 | 92.5 | 111.1 | 0.83 |
| Hologic | 15 | 10 | 5 | 0 | 0.18 | 715 | 30 | 40.2 | 56.2 | 0.72 |
| McKesson Medical-Surgical | 15 | 14 | 1 | 0 | 1.17 | 1055 | 30 | 43.2 | 139.0 | 0.31 |
| Metrohm USA | 15 | 0 | 15 | 0 | 0.00 | 0 | 30 | 0.1 | 65.4 | 0.00 |
| Monobind Inc. | 15 | 1 | 14 | 0 | 0.11 | 1581 | 30 | 54.7 | 67.7 | 0.81 |
| Neogen | 15 | 0 | 15 | 0 | 0.00 | 0 | 30 | 0.1 | 46.6 | 0.00 |
| Nova Biomedical | 15 | 14 | 1 | 0 | 0.03 | 159 | 30 | 32.3 | 59.2 | 0.55 |
| QuidelOrtho | 15 | 11 | 4 | 0 | 0.42 | 1958 | 30 | 40.5 | 37.5 | 1.08 |
| Sartorius | 15 | 6 | 9 | 0 | 0.14 | 628 | 30 | 42.2 | 114.2 | 0.37 |
| Sebia | 15 | 12 | 3 | 0 | 0.25 | 606 | 30 | 66.9 | 135.8 | 0.49 |
| Shimadzu Scientific Instruments Inc. | 15 | 5 | 10 | 0 | 0.08 | 843 | 30 | 40.3 | 113.4 | 0.36 |
| Sysmex America | 15 | 14 | 1 | 0 | 0.38 | 1006 | 30 | 49.3 | 128.1 | 0.38 |
| Thermo Fisher Scientific | 15 | 11 | 4 | 0 | 0.21 | 1323 | 30 | 41.2 | 60.8 | 0.68 |

*(Metrohm USA specifically re-verified per the standing instruction to check the stale 8-entity file's 0/15: it is still 0/15 here, but now root-caused to the robots.txt UA bug above, not to the old 15-page crawl-budget/pre-locale-dedup-fix artifact the stale file's context implied — a different, more specific explanation than "historical, no longer relevant.")*

**What remains to close the gap (unchanged in kind from the original proposal, now concrete):**
1. Fix the robots.txt-fetch UA bug (`playwright_pool.py:79-86`) and re-run this exact harness — expected to recover ~60 pages / turn all 4 zero-ok entities into normal ones, which would flip bar item 4 to PASS.
2. George runs `diagnostics/backend_compare.py --baseline adlm-outputs/validation_sample_run_2026-07-03.xlsx --entities "<all 25>"` (same command as this session) from the Sagentia work laptop / network, so bar item 5's "no blocks" claim is tested against a Sagentia-reputation IP, not a residential one.
3. George runs the full `build_validation_sample_workbook.py` input through the real pipeline with `ACQUIRE_TOOL=playwright_pooled` on the work laptop (VPN + `.env` present there), and diffs its Matrix sheet's populated-cell counts against `validation_sample_run_2026-07-03.xlsx`'s Matrix for Q2/Q3 — this is the only way to score bar item 1, the central one.
4. Investigate the Aladdin-style silent-generic-content failure mode further (item 3 above) before trusting `playwright_pooled` in production even if the other bars pass — an "ok" status alone is not proof of correct per-page content when a site is a hydration-heavy SPA.
