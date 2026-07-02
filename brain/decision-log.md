# Decision Log — AI Extraction Pipeline

**Append-only. Newest entries at top. One entry per architectural decision.**
**Format: Context → Options considered → Decision → Why (complete) → Status/Result**

-----

## 2026-07-02 — Entity-level parallelism + global LLM-call cap + LLMAPI 5xx retry

**Context:** The 25-company validation run took 36m 44s; Acquire was ~75% of wall clock and doubly serial (pages within `crawl_entity`, entities within `run_pipeline`). 182 projection ≈ 4.5 h. Full analysis: `brain/proposals/runtime-depth1.md`.

**Decision (3 coupled changes):**
1. `run_pipeline` processes URL specs concurrently (`PIPELINE_ENTITY_WORKERS = 4`). Per-spec work moved to `_process_url_spec`, which accumulates into a **local** diag and returns it; the main thread merges results in original spec order, so diagnostic sheets stay deterministic and the old index-slice annotation race is designed out. One spec = one seed domain → per-domain request rate unchanged (politeness preserved by construction).
2. Global semaphore on extractor LLM calls (`EXTRACT_MAX_CONCURRENT_CALLS = 16`, `src/extract.py`). Without it, worst case is 4 entity × 4 page × 8 chunk = 128 concurrent proxy calls; the proxy 502'd once under single-entity load already. Cache hits don't take a slot.
3. `LLMAPI.call` retries once on 5xx (5 s wait). Previously a 502 silently blanked that chunk's cells. Timeouts keep the existing no-retry contract; 4xx not retried. Tests: `tests/test_llmapi_retry.py`.

**Rejected:** within-entity concurrent fetching (raises per-domain rate — revisit only if entity parallelism is insufficient); retry-on-timeout (already handled deliberately).

**Status:** Applied. Expected 182 wall clock ~50–70 min. Worker count ceiling = Firecrawl plan concurrency — confirm before raising above 4.

-----

## 2026-07-02 — Crawl link hygiene: locale-variant dedup (new) + score-aware link cap (fixes 2026-07-01 known issue)

**Context:** Validation run showed the 15-page budget consumed by translated copies of the homepage (Bruker 9/15: /fr /ko /de /pl /es /pt /ru /zh /it; Metrohm ~10/15; QuidelOrtho ~12/15) — they score ~0.55–0.63 because they carry the same nav text. Costs both runtime and Q1/Q4 recall (they crowd out About/locations/news). Separately, the recorded 2026-07-01 issue: `CRAWL_MAX_LINKS_PER_PAGE=30` was a DOM-order slice applied inside the discovery functions, before scoring.

**Decision:**
1. **Locale dedup** (`CRAWL_LOCALE_DEDUP = True`): `_locale_key()` collapses pure locale path segments (`^[a-z]{2}([_-][a-z]{2})?$`, incl. `xx.html`/`xx_yy.html` filenames) to a placeholder; candidates whose key matches an already-fetched/queued page are dropped, and only one variant per discovery batch survives. Pattern-based, no site list. Query strings kept in the key (so `index.php?product=N` pages never collapse); sites nesting all content under one locale prefix (aladdinsci `/us_en/…`, sebia `/en-us/…`) keep distinct pages distinct. Known trade-off (documented in code): a genuine 2-letter content segment is treated as a locale — first variant wins.
2. **Score-aware cap:** truncation removed from `_discover_links_from_markdown/_html`; `crawl_entity` now slices top-30 **after** scoring (every scorer path returns best-first). A footer About link past the 30th anchor now reaches the scorer.

**Why now, together:** both change which links are followed, and the next sample run validates them jointly before the 182 (same discipline as the rawHtml fix). `CRAWL_LOCALE_DEDUP=False` gives the before/after control.

**Status:** Applied; tests in `tests/test_crawl_relevance.py` (locale-key collapse/keep cases from the actual validation-run URLs; 41-link discovery no-truncation). **Requires re-validation on the 25-sample before the 182** — expect fewer wasted fetches and better Q1/Q4 page mix; Q1 starvation may need more than this (open).

-----

## 2026-07-01 — Crawl link discovery reads Firecrawl raw HTML, not markdown (validated)

**Context:** The clean-homepage comparison run left Tosoh/Surmodics Q1-blank (R&D location) despite clean www seeds. Root cause: Firecrawl's content pipeline drops some nav/footer links. Verified literally on `www.surmodics.com` — `/about-surmodics`, `/our-company`, `/contact-us` are ABSENT from both the cached Firecrawl markdown (11 KB, grep zero matches) and `result.html` (2.0 MB, cleaned), but PRESENT in `result.raw_html` (3.0 MB). Those links never entered the crawl candidate pool, so no scorer or allowlist could recover them.

**Options considered:**
1. URL-pattern allowlist (always follow about|contact|locations) — REJECTED: can't allowlist a link that was never discovered.
2. Better markdown link parsing — REJECTED: the links aren't in the markdown at all.
3. Discover links from Firecrawl's rendered HTML via the existing `_discover_links_from_html` path.

**Decision:** Option 3, scoped to the Firecrawl backend. Added `_fetch_firecrawl_doc` (requests `formats=["markdown","rawHtml"]`, returns `result.raw_html`); `_discover_links` prefers HTML for `acquire_tool == "firecrawl"`. `_fetch_firecrawl` (markdown-only, str) left intact for `_FETCHERS`/`fetch_page_raw`; 4 firecrawl smoke tests repointed to `_fetch_firecrawl_doc`.

**Why / trade-off (explicit):** This re-enables the parent-element "nav-soup" link context that the 2026-06-16 decision (`include_links=True`) moved away from in favour of ±120-char prose context. Accepted because (a) the affected links are otherwise missed entirely, and (b) the crawl/filter scorer measured ~AUC-0.5 on this task, so weaker context has little marginal cost. **Scoped to Firecrawl** — the local backend keeps its markdown path + prose context, unchanged. Note: `.html` (cleaned) drops the links too; only `rawHtml` preserves them — the fix required the raw format specifically.

**Result (validated on the 6-company clean-homepage sample, Surmodics cache cleared so it re-fetched fresh; other 5 were cache hits and unchanged):** Surmodics crawl candidates went 5 → 19 discovered, 16 followed. `/our-company` (0.57), `/contact-us` (0.57), `/ireland-facility` (0.55), `/about-surmodics` (0.54), `/careers` (0.62) all discovered AND followed. Surmodics Q1 recovered from `No data found` → Minnesota HQ + Ireland facility (Eden Prairie, MN / Ballinasloe, Co. Galway). The 30-link DOM-order cap did NOT bite (19 < 30), so `/about-surmodics` survived — the score-aware-cap change remains a recorded-but-unneeded follow-up.

**Status:** Applied (commit 322d0ec) and validated. All 182 companies fetch fresh in the full run, so all exercise the new discovery path.

-----

## 2026-07-01 — Known issue: brittle fixture in test_aggregate_list_column_no_conflict (fix queued)

**Context:** Running the full `tests/test_smoke.py` during the link-discovery fix validation surfaced one failure: `test_aggregate_list_column_no_conflict` asserts `num_unique_values == 5` but gets `1`. Confirmed unrelated to the Acquire diff — reproduces identically on both machines and `aggregate.py` was untouched.

**Root cause:** The fixture builds five values `"claim 0".."claim 4"`. `fuzz.token_sort_ratio("claim 0", "claim 1") = 85.71`, which is `>= _DEDUP_RATIO (85)`, so aggregate's fuzzy near-duplicate dedup collapses all five into one. This is not a production bug — real distinct claims don't collide at 86% token_sort_ratio; the fixture just chose near-identical single-token strings. It broke on **2026-06-29** when `_DEDUP_RATIO` was lowered 95→85 (Oatly near-paraphrase collapse); at 95 the strings survived (85.7 < 95) and the test passed. Nobody updated the fixture then.

**Decision:** Fix the fixture, not the product. Replace `"claim {i}"` with genuinely distinct-topic strings (e.g. "solar power", "wind energy", "recycled packaging", …) so the test exercises list-column non-conflict without tripping the fuzzy-dedup threshold.

**Status:** FIXED after Surmodics validation — fixture now uses distinct-topic strings (max pairwise token_sort_ratio ~46); test passes. Own commit, separate from the discovery fix.

-----

## 2026-07-01 — Known secondary issue: crawl link cap truncates before scoring (recorded, not fixed)

**Context:** The clean-homepage comparison run (6 ADLM diagnostics companies, depth 1, passthrough) was used to isolate whether Q1 (R&D location) starvation is seed-URL-driven or a weak link scorer. Investigating why Tosoh/Surmodics stayed Q1-blank surfaced a discovery-layer issue worth recording before it's forgotten.

**The issue:** `CRAWL_MAX_LINKS_PER_PAGE = 30` is applied as a plain slice (`candidates[:30]`) at the END of both `_discover_links_from_markdown` and `_discover_links_from_html` — i.e. in DOM/markdown order, **before** the relevance scorer runs. So an About/Contact/locations link that sits past the 30th anchor on the page (common: footer nav, or a long product mega-menu ahead of the footer) is dropped before the scorer can ever rank it. The cap is a pre-scoring positional truncation, not a keep-the-top-30-by-score.

**Why it matters for the 182:** Q1 and Q4 depend on reaching About/locations and Press pages. On link-heavy homepages those links are frequently in the footer, after 30+ product/nav anchors. This silently caps recall on exactly the pages Q1/Q4 need — and it's invisible in the Crawl Candidates log because dropped links never become candidates.

**Decision:** Record as a known secondary issue; do NOT fix now. The gating fix is the primary discovery change (Firecrawl markdown flattens some nav links → route discovery through rendered HTML), which must be validated on the sample first. Bundling a cap change into that would confound the sample re-run's signal.

**Candidate fix when addressed (not now):** either raise `CRAWL_MAX_LINKS_PER_PAGE`, or make the truncation score-aware (score all discovered candidates, then keep the top-N by score instead of the first-N by DOM order). The latter is the principled fix but needs its own before/after on the sample.

**Status:** ~~Recorded only. No code change.~~ **FIXED 2026-07-02** — score-aware cap applied in `crawl_entity` after scoring (see 2026-07-02 link-hygiene entry). The rendered-HTML discovery fix landed separately (322d0ec) and was validated first, as planned.

-----

## 2026-06-30 — ADLM directory scraper: primary URL-acquisition path

**Context:** The 182 filtered clinical-diagnostics input companies need official URLs. The ADLM 2026 exhibitor directory lists every exhibitor with its company-declared website, so scraping it is more accurate and free vs the Firecrawl resolver.

**Approach:** Standalone `adlm_scraper.py` (plain `requests` + BeautifulSoup, no API/Firecrawl). Three phases: (1) paginate the directory and dump all exhibitors; (2) name↔name fuzzy-match the 182 inputs to directory rows; (3) fetch the matched detail pages and pull `official_url`/`linkedin_url`.

**Pagination finding:** Not static — AJAX POST to `/index.php` (`paginationHandler`, `mId=2`, `limit/offset`), chaining rotating `tk`/`tm` CSRF tokens; JSON `data` holds the url-encoded HTML fragment. 716 exhibitors over 18 pages.

**Two bugs caught by post-run audit (both would have silently corrupted output):**
1. **False-100 matches** — reusing `confidence.py`'s legal-suffix stripping (built for name↔domain) turned `AB Medical`→`medical`, `SA Scientific`→`scientific`; those stubs then subset-matched longer names at `token_set_ratio`=100. Fixed: light normalisation (no suffix stripping) + full-string `ratio`/`token_sort_ratio`. First run falsely reported 182/182 at score 100.
2. **Footer brand-bar leak** — the platform renders ADLM's own social links (class `social_link`, `…/myADLM`) on every detail page; "first external link = official" + a case-sensitivity hole grabbed `facebook.com/myADLM` as a company URL, faking 182/182. Fixed: skip `social_link` anchors + platform hosts, reuse `confidence.is_blocked`. Surfaced the one genuine no-URL exhibitor (BizLink Elocab).

**Decision:** Directory scrape is the primary URL source; resolver is fallback for exhibitors whose ADLM page declares no URL. One verified manual override (`Currier Plastics, Inc.`→`/co/currier`, directory listed it as just "Currier").

**Result:** 716 exhibitors scraped; 182/182 matched; **181/182 official URLs directory-sourced + 1 manual web lookup** (BizLink Elocab → `elocab.bizlinktech.com`, tagged `source=manual_web_lookup` in `matched_official_urls.csv`). 24 also got LinkedIn.

**Status:** Done — URL acquisition complete.

-----

## 2026-06-30 — Company-URL resolver added, demoted to fallback

**Context:** Need to resolve exhibitor company names to official URLs for the ADLM pipeline. Initial approach: standalone search resolver (`src/resolve/`) — Firecrawl search + offline rapidfuzz/keyword scoring, with confidence and `needs_review` flags.

**Result on 182 companies:** 181 resolved, ~15% flagged `needs_review`. Makes confident errors on ambiguous/obscure names.

**Safety fix landed:** removed all direct-internet search (a Bing-routed version surfaced unsafe results for ambiguous names); now Firecrawl-only. Unresolved companies are flagged rather than guessed.

**Decision:** Demoted to fallback. Primary method is scraping the ADLM exhibitor directory (static HTML with company-declared official URLs — more accurate, free, no confidence risk). Resolver (`resolve_urls.py`) used only when a company's ADLM card has no URL. Default mode is search-only (~1 Firecrawl credit/company); homepage fetch is opt-in via `--fetch`.

**Status:** In use as fallback.

-----

## 2026-06-29 — Plant-milk evaluation cycle closed (tagged v1.0-plant-milk-eval)

**Context:** End-of-cycle state summary for the plant-milk brand evaluation. This is not a new architectural decision — it records the final artefact versions, the fixes landed this cycle, and the headline metrics, so the next cycle starts from a known baseline. HEAD tagged `v1.0-plant-milk-eval`.

**Final state of artefacts:**

- **Ground truth v3** — 102 sustainability claims, 10 parent company, 29 milk types across the 10-brand set.
- **Pipeline output v4** — verify-layer fix landed: Option A (markdown/whitespace normalisation before fuzzy compare, exact substring check untouched) + Option C (soft anchor threshold for long quotes: ≥100 chars, both 20-char anchors literal in page text, `partial_ratio` ≥ 68). Config: `VERIFY_THRESHOLD_SOFT = 68`, `VERIFY_LONG_QUOTE_MIN = 100`.
- **Pipeline output v7** — aggregate/Matrix fixes landed: `_DEDUP_RATIO` lowered 95 → 85 to collapse Oatly near-paraphrase duplicates; Matrix renderer now reads `agg_cell.value` instead of `agg_cell.evidence` (so `_DEDUP_RATIO` actually takes effect in output); set-union for list columns (`_UNION_LIST_COLS`, currently `{"Plant milk types"}`) merges comma-separated item lists across sources into one canonical value; `_make_matrix_df` falls back to `agg_cell.verified` for synthesised union values absent from the evidence lookup.

**Result — eval report v5, pass 2:** overall F1 = 0.88 (R = 0.91, P = 0.88), hallucination rate = 0. Sustainability column F1 = 0.66 (the hardest column; the headline F1 is carried by the easier parent-company and milk-type columns).

**Known limitations carried forward (candidate next-cycle work):**

1. **Oatly chunked-extraction redundancy** — the 8,000-char chunking over the long Oatly sustainability report still produces overlapping near-duplicate claims across chunk boundaries; `_DEDUP_RATIO = 85` collapses many but not all, and the union logic does not apply to free-text claim columns.
2. **Merge-passenger aligner artefact** — the greedy 1:1 + quote_id one-to-many exception occasionally lets a low-value AI claim ride along on a shared quote_id group, slightly affecting precision attribution.
3. **One verify false negative — Oatly GHG table-caption quote** — a quote drawn from a table caption fails verification because the cached markdown renders the caption text in a form the fuzzy/anchor checks don't recover. Single known case this cycle; not yet generalised into a fix.

**Status:** Cycle closed and tagged. No code changes in this log entry — record only.

-----

## 2026-06-24 — Agentic verification rejected as scope-creep

**Context:** Considered adding an LLM-based keep/reject agent in the Verify layer to improve precision and reduce redundancy. The premise was that precision (0.73) was being hurt by “too many weak or duplicate claims.”

**Options considered:**

1. Add agentic LLM keep/reject filter in Verify
2. Sharpen the extraction prompt’s inclusion criteria (already done)
3. Leave as-is and let the deterministic rapidfuzz baseline stand

**Decision:** Rejected option 1. Do not build now.

**Why:** The premise was empirically wrong. Looking at the actual metrics: strict precision = 0.73, distinct precision = 0.74 — the gap between the two is tiny (0.01), which means redundancy is NOT the main driver of the precision gap. The 58 “gap” claims are predominantly source-verified real claims and granularity splits, not weak duplicates. Adding an LLM agent to keep/reject those claims would lower recall (the priority metric) without fixing the actual problem. More fundamentally: adding a non-deterministic LLM judge to the one layer kept clean and reproducible would make the whole pipeline non-reproducible — running it twice on the same input could produce different verified claim sets. The dissertation’s contribution rests on the deterministic verify → score chain being trustworthy. Injecting an opaque LLM decision at verification breaks that. If ever built, it must be evaluated AGAINST the deterministic baseline using the Stage 10 framework, not built as a one-directional “improvement.”

**Future work entry:** “Agentic LLM-as-judge verification — candidate Could-tier experiment. Run both deterministic (rapidfuzz) and agentic verifier on the same input. Score both against ground truth via Stage 10 framework. Report precision/recall/F1 delta. Do not build without this comparison.”

-----

## 2026-06-24 — Stage 10: dual precision (strict + distinct) chosen over single figure

**Context:** Scoring decision — when an AI claim is a restatement of an already-matched GT claim, how does it count toward precision?

**Options considered:**

1. Strict only: every unmatched AI claim = false positive (penalises pipeline for repeating true facts)
2. Distinct only: drop redundant restatements from denominator (hides redundancy)
3. Report both: strict precision AND distinct precision, with the gap quantifying pipeline redundancy

**Decision:** Report both.

**Why:** The gap between strict and distinct precision is a real, reportable finding. Strict precision (every unmatched AI claim counts against you) answers “how clean is the raw output.” Distinct precision (restatements of already-matched GT claims dropped) answers “how complete is the distinct-fact coverage.” The gap between them quantifies how often the pipeline repeats verified facts — which is itself useful information about cost (you’re paying to extract the same fact multiple times from different chunks) and output quality (analysts see redundant claims). In the actual data, the gap is tiny (0.73 vs 0.74), which itself is a finding: redundancy is NOT the main precision problem. If you reported only one number you’d hide this. The “report both” approach is also the most defensible in a viva — you can’t be accused of choosing the flattering number if you show both and explain what each measures.

-----

## 2026-06-23 — Conflict detection gated on question type (list vs single-answer)

**Context:** `has_conflict = len(unique_values) > 1` in aggregate.py was firing on every cell with multiple values — which meant every sustainability cell with 5+ claims was flagged as conflicted. The Matrix was flooded with false (sources conflict) labels.

**Options considered:**

1. Keep the existing logic, accept the noise
2. Require ≥2 VERIFIED values for a conflict (Claude Code’s Option V)
3. Gate conflict detection on question type: list questions never conflict, single-answer questions can

**Decision:** Option 3.

**Why:** Option 2 was close but still wrong — it would still fire on list questions when two distinct verified values exist (which is the *correct* state for a list question). The root problem is semantic: “conflict” means something different depending on question type. For a list question like “What sustainability claims does the brand make?”, multiple values is the expected, correct, desired state — each claim is a separate fact, not a contradiction of another. You *want* 30 Oatly sustainability claims in one cell. For a single-answer question like “Who is the parent company?”, there should be exactly one value, so two different values from two different pages is a genuine signal that something is off. The predicate `_is_list_column(instruction)` reads the question instruction text to classify: list questions contain “comma-separated”, “deduplicated”, “list”, “for each”, or match `\bone\b.{1,30}\bper\b`. Validated against all three production questions: Sustainability (“For each claim return one concise sentence”) = list ✓, MilkTypes (“comma-separated, deduplicated”) = list ✓, Parent company = single-answer ✓.

-----

## 2026-06-23 — “None (not disclosed on site)” sentinel treated as null everywhere

**Context:** The pipeline sentinel value “None (not disclosed on site)” — returned by the LLM when a question’s answer is not on the page — was being stored and processed as if it were a real claim value. This caused: (1) false conflicts (aggregate saw “Danone” and “None (not disclosed)” as two different answers → flagged conflict), (2) false hallucinations in metrics (the evaluator counted a spurious AI-null as a fabricated claim), (3) inflated precision denominators.

**Options considered:**

1. Strip the sentinel at aggregation time only
2. Strip it at evaluation time only
3. Define it as a null sentinel once, enforce everywhere (aggregate.py, aligner.py, metrics.py)

**Decision:** Option 3 — `_is_null_sentinel()` as a shared function, applied consistently.

**Why:** The sentinel is conceptually an absence, not a value. “None (not disclosed on site)” means “the LLM read the page and found no answer.” It says nothing about what the answer is — it explicitly says there is no answer on this page. Treating it as a value is like treating a blank cell in a spreadsheet as the word “blank.” The bug was discovered concretely: Silk’s parent-company cell showed “Danone” from the about-us page and “None (not disclosed)” from the products page — because the products page genuinely doesn’t mention the parent. Flagging that as a conflict was nonsensical. In the evaluation, the sentinel caused 4 ParentCompany cells to be counted as hallucinations when they were correct null-outputs. After reclassification: hallucination rate → 0. The honest headline is “zero fabricated content” — but only if you properly define what fabrication means (a claim not in the source) rather than conflating it with “a page that correctly found nothing.”

-----

## 2026-06-22 — Greedy 1:1 matching with quote_id exception for the evaluation aligner

**Context:** The Stage 10 evaluation needs to align AI-extracted claims against ground-truth claims. The core question: should one AI claim be allowed to “credit” multiple GT claims?

**Options considered:**

1. Hungarian bipartite matching (strict 1:1, optimal assignment)
2. Unrestricted GT-centric matching (each GT claim independently picks its best AI candidate — one AI claim can credit N GT claims)
3. Greedy 1:1 with a specific exception for GT rows sharing a quote_id

**Decision:** Option 3.

**Why:** Option 1 (Hungarian) was rejected because it can’t handle a real case: when the AI returns one sentence that genuinely covers two GT facts from the same source sentence (e.g. Chobani’s “diverts 90% of waste from landfill” appears as two GT rows — “committed to 90% waste diversion” and “on track toward 90% waste diversion” — sharing a quote_id because they come from the same verbatim sentence). Strict 1:1 would require two separate AI claims to cover both rows, which is unreasonable — the AI correctly produced one claim, it should get credit for both rows. Option 2 (unrestricted) was rejected because it inflates recall: a single vague AI claim (“the company cares about sustainability”) could become the best match for many GT rows, giving near-perfect recall from one low-quality claim. The quote_id mechanism solves this precisely: the one-to-many exception is allowed only when GT rows are explicitly flagged by the analyst as coming from the same source sentence. That’s an analyst judgment call, not a cosine similarity guess. Everything else is 1:1, which is the anti-recall-inflation guard.

-----

## 2026-06-22 — Filter passthrough mode for the extraction evaluation

**Context:** The extraction evaluation (RQ1) was designed to measure “given the right page, does the AI extract the right claims?” But running with FILTER_THRESHOLD=0.55, Ripple’s sustainability question was filtered out (scored 0.4962 < 0.55) even though the page contained 5 real sustainability claims confirmed in the ground truth. The filter was silently suppressing real answers and making what was actually a filter failure look like an extraction failure.

**Options considered:**

1. Lower FILTER_THRESHOLD to ~0.45 to recover the suppressed pages
2. Add a passthrough mode that bypasses filtering entirely for the evaluation
3. Accept the losses as legitimate filter decisions and score around them

**Decision:** Option 2 — `FILTER_MODE = "passthrough"` for the extraction evaluation.

**Why:** Option 1 would change the production filter threshold to compensate for an evaluation artifact — that’s tuning your system to make your evaluation easier, which is backwards. Option 3 would mean your evaluation is measuring “how well does the pipeline extract from pages that Filter didn’t suppress” rather than “how well does the pipeline extract from the right pages” — the second question is what RQ1 actually asks. The fundamental insight: for the extraction evaluation, every input URL was hand-picked from the ground truth at depth 0. Every page is known-relevant by construction. There are no junk pages to filter out. The filter’s job is to save cost when crawling produces a mix of relevant and irrelevant pages — here there are no irrelevant pages. So filtering can only hurt recall and cannot help it. Passthrough in this context is not disabling the filter — it is the architecturally correct setting for an evaluation where acquisition was held constant. The config flag also serves a second purpose: when you later run backend comparisons or crawl-depth evaluations, you need to be able to disable the thin-content fallback between Firecrawl and Playwright, so the flag pattern was already the right design.

-----

## 2026-06-22 — Aggregate layer wired into Matrix output (previously dead code)

**Context:** `aggregate_cells()` in aggregate.py computed deduplication, ranking (exact > fuzzy > none), conflict detection, and source counting — but `io_excel.py` read `row.all_cells` (raw per-page contributions) instead of `row.cells` (the aggregated result). Every sheet writer had the same guard: `src = row.all_cells if row.all_cells else row.cells` — and since all_cells is always non-empty when data exists, row.cells was never read. The aggregated result was fully dead.

**Options considered:**

1. Leave as-is (aggregate stays dead, Matrix built from raw cells)
2. Switch all sheets to aggregated cells
3. Split by purpose: Matrix reads aggregated, Provenance stays granular on raw cells

**Decision:** Option 3.

**Why:** Option 2 was rejected because the joined-source-URL trap would break Summary’s page count (aggregate.py stored `source_url = "; ".join(sorted(source_urls))` — one joined string, so `len({c.source_url for c in src})` would count it as 1 page regardless of how many pages contributed). Option 1 meant the work already done in aggregate (dedup, ranking, conflict detection) produced no output — a waste and a correctness problem, since the Matrix was showing duplicate claims from multiple chunks. Option 3 works because the two sheets have different jobs: Matrix is the deliverable, the clean answer per entity × question, which should be deduplicated and ranked. Provenance is the audit trail, every piece of evidence with its source, which must be granular. The data model already supports both — `row.all_cells` preserves per-page-per-quote granularity, `row.cells` is the aggregated view. Separating them means an analyst sees the clean answer in the Matrix and can trace it back through Provenance. The conflict label (sourcing from `has_conflict` on the aggregated cell) became visible for the first time.

-----

## 2026-06-21 — Extract prompt hardened: one verbatim sentence per claim, list-quote forbidden

**Context:** Production runs showed two failure modes in the quote field: (1) the model sometimes returned a multi-sentence paragraph blob as a single “quote,” (2) the model sometimes returned `"quote": ["sentence A", "sentence B"]` — a list of quotes for one claim — which got concatenated into a wall of text. Both caused Verify to fail (exact 1,500+ char strings don’t appear verbatim in source pages).

**Options considered:**

1. Fix at parse time: split list-quotes into separate SourceQuotes, detect and truncate blobs
2. Fix at prompt level: explicitly forbid list-quotes and multi-sentence strings
3. Fix at the model level: switch from Azure gpt-4.1-mini (which ignores instructions) to GPT-5.5

**Decision:** Option 2 as the immediate fix, Option 3 as the durable fix.

**Why:** Option 1 (parsing fix) was the riskiest path. Splitting a list of quotes is safe if you can keep each quote paired with its claim. But for the blob case — a 1,500-char paragraph returned as a single string — there’s no reliable way to split it back into individual supporting sentences without making up content. The model was supposed to provide the minimal span; you can’t recover that post-hoc. The parse fix would also give false confidence: clean-looking short quotes that were actually positionally-mismatched to the wrong claim. Option 2 (prompt hardening) attacks the problem at its root. The key change was removing the hedge “where possible” from the original quote instruction — that phrase was explicitly licensing the model to approximate. The new instruction requires character-for-character copying, explicitly forbids lists, explicitly forbids paragraph blobs, and instructs that if a claim is supported by multiple sentences, it should become multiple {value, quote} entries. Option 3 (model switch) turned out to be the durable fix: GPT-5.5 via Power Automate produced 0 walls of text (>1000 chars) on the same input where Azure gpt-4.1-mini produced 50. The prompt fix is still correct to keep because it establishes the right expectation regardless of which model runs.

-----

## 2026-06-20 — Extract: chunked extraction replacing [:7000] truncation

**Context:** The extraction prompt was hardcoded to pass only the first 7000 characters of each page to the LLM. The Oatly sustainability report alone is 113,751 chars. This meant 93% of the content was silently discarded before the LLM ever saw it.

**Options considered:**

1. Raise the truncation limit (e.g. to 30,000 chars, the model’s context limit)
2. Chunk the page and extract from each chunk independently, then merge
3. Use a summarisation pre-pass to compress the page before extraction

**Decision:** Option 2 — EXTRACT_CHUNK_SIZE=8000, EXTRACT_CHUNK_OVERLAP=200.

**Why:** Option 1 would hit model context limits on very long pages and would make each LLM call more expensive without guaranteeing better coverage (one enormous context is harder for the LLM to scan than multiple focused ones). Option 3 introduces a second LLM call per page and risks summarisation compressing away the exact verbatim phrases needed by Verify. Option 2 is the principled approach: process each chunk independently (the same entities × questions prompt on each 8,000-char window), then merge results across chunks. The 200-char overlap between chunks prevents claims that straddle a chunk boundary from being lost. The cost scales with page length rather than with matrix size (one call per chunk covers all entities × all questions for that chunk). The concrete impact was immediate: claims found for the Oatly sustainability report went from near-zero (only what was in the first 7000 chars) to 66 claims. Concurrent chunk processing (EXTRACT_MAX_WORKERS=8) kept the runtime acceptable.

-----

## 2026-06-20 — Filter threshold: 0.55 (empirically determined, not intuitive)

**Context:** Initial FILTER_THRESHOLD = 0.35 was chosen on the assumption it would be “lenient enough not to drop anything.” The filter diagnostic showed it was so lenient that 55/55 pages scored above it on all 3 questions — the filter was a complete no-op.

**Options considered:**

1. Keep 0.35 as a safety net (accept that filtering does almost nothing for broad questions)
2. Raise to 0.55 to create real separation, accept some filtering risk
3. Use per-column thresholds

**Decision:** 0.55, with the passthrough mode as a safety valve for evaluation runs.

**Why:** The score distribution from the filter diagnostic was decisive: nomic-embed-text cosine similarities for commercial brand pages against these question types cluster between 0.40–0.72. The score for the most obviously irrelevant page (recipe page, sustainability question) was 0.455. The score for the most relevant page (sustainability report, sustainability question) was 0.538. At 0.35, everything passes. At 0.55, real separation begins: recipe pages and pure product pages lose sustainability routing, while sustainability reports keep it. The risk at 0.55 was confirmed real: Ripple’s our-story page scored 0.4962 on sustainability and was filtered out, even though the GT confirmed 5 real claims on that page. This led directly to the Filter passthrough mode — 0.55 is the right production threshold for general use, but for the extraction evaluation (where every URL is hand-picked), passthrough is the right setting. Option 3 (per-column thresholds) is correct in principle — 0.55 was too aggressive for broad sustainability questions (18/55 pages) but about right for parent company (41/55) — but adds config complexity and was deferred since passthrough mode covers the evaluation case cleanly.

-----

## 2026-06-20 — Filter: chunk-level scoring with max over chunks (not page-level embedding)

**Context:** Initial Filter embedded the first 2000 chars of each page. For the Oatly sustainability report (113,751 chars), the first 2000 chars are a title and intro that look like every other Oatly page. A recipe page’s first 2000 chars look similar to a sustainability page’s opening. Page-level embedding was blurring the distinction the filter needed to make.

**Options considered:**

1. Keep page-level embedding, raise threshold more aggressively
2. Embed full page (no truncation)
3. Chunk the page (~1000 chars), embed all chunks, take max cosine per question

**Decision:** Option 3 — FILTER_CHUNK_SIZE=1000, capped at 100 chunks per page.

**Why:** Option 1 (raise threshold) risks false negatives — we’d already seen Ripple’s sustainability page filtered out at 0.55 on a full-page embedding that diluted the signal. Option 2 (no truncation) would produce an average of the page’s content, which is also wrong — a sustainability report’s average embedding is pulled down by its table-of-contents, boilerplate, legal disclaimers, and footnotes. The max-over-chunks approach mirrors the same insight applied to per-question max scoring in the crawler: **relevance is local, not global**. A recipe page genuinely has no 1000-char window that scores high on “sustainability claims.” A sustainability report has many windows that score very high. Max over chunks preserves that distinction; averaging destroys it. The 100-chunk cap prevents unbounded cost on very long pages (at 8,000 chars/chunk that’s 100,000 chars processed per page, covering all but the longest PDFs). The diagnostic confirmed the fix: filter scoring became meaningfully more accurate, with sustainability scores rising on report pages and staying low on recipe and product pages.

-----

## 2026-06-19 — Filter: keyword gate as second independent signal (OR logic)

**Context:** Even with chunk-level scoring at 0.55, nomic-embed-text’s compressed similarity range (everything between 0.40–0.72) made it hard to create reliable separation. The semantic signal alone was insufficient for short, generic question labels.

**Options considered:**

1. Rely on embedding alone, accept imperfect separation
2. Add a keyword gate: if question keywords appear in page text, route regardless of embedding score
3. Replace embedding with keyword matching entirely

**Decision:** Option 2 — OR logic: relevant if (max_chunk_score ≥ threshold) OR (question keywords in page text).

**Why:** This is the hybrid retrieval pattern used in production search systems (dense + sparse retrieval, each compensating for the other’s blind spots). Dense retrieval (embedding) catches semantic relevance when exact words don’t match — “carbon footprint” matching a page about “GHG reduction targets.” Sparse retrieval (keyword matching) catches cases where the embedding model misses an obvious lexical match. The two signals fail in different cases, so combining them is strictly more robust. The OR logic ensures a page is never dropped when either signal fires — which preserves completeness. The keyword extraction is simple: words >3 chars from the question text, standard stopwords removed. The gate works best for specific terms (“certification”, “organic”) and less well for generic terms (“milk” appears on almost every brand page, so Plant milk types is almost never filtered). This is a documented known limitation, not a bug — it reflects the generality of the question label. A more specific question like “What USDA Organic or B Corp certifications has this brand received?” would create much sharper separation.

-----

## 2026-06-18 — Firecrawl chosen as default fetch backend

**Context:** Five-backend empirical comparison on 4-brand test set (60 pages: Oatly, Ripple, Chobani, Silk at depth 1).

**Results:**

|Backend   |Runtime|Ok/Total|Avg chars|Key finding                                            |
|----------|-------|--------|---------|-------------------------------------------------------|
|local     |342s   |44/60   |5,211    |Silk broken (465 chars), Chobani product pages 49 chars|
|requests  |70s    |60/60   |6,167    |No JS rendering                                        |
|playwright|438s   |60/60   |12,031   |Too slow for scale                                     |
|firecrawl |231s   |55/60   |19,534   |Found Silk sustainability at depth 2                   |
|sgai      |363s   |0/60    |—        |Total failure, all API errors                          |

**Decision:** Firecrawl as default. Local retained for data-privacy contexts. SGAI dropped as fetcher entirely.

**Why:** The decisive factor was not just content quality but **discovery quality**: Firecrawl found Silk’s `/about-us/sustainability` and `/about-us/b-corp` pages at depth 2 — pages that no other backend found and that contained key evaluation claims. The avg chars advantage (19,534 vs 5,211 for local) translates directly to extraction coverage. SGAI’s complete failure (0/60 pages, API errors on all test entities) removed it from consideration entirely. Local backend retained — not because it’s competitive on quality but because it keeps data on the Sagentia network, which matters for client data. The local backend’s known weaknesses (Silk broken, Chobani thin) are documented as findings rather than defects, because they reveal real constraints of the privacy-preserving approach. Playwright retained as a fallback mechanism (thin-content detection) rather than a primary backend — it produces good content but at 438s for 60 pages it’s impractical as the default.

-----

## 2026-06-18 — Playwright: networkidle → domcontentloaded + 2s fixed delay

**Context:** The Playwright fallback was using `wait_until="networkidle"` — waiting for the page’s network activity to fully settle before extracting content. Silk’s homepage, built on React, never reaches networkidle because it maintains background connections. Every Silk Playwright attempt timed out after 33 seconds. The 4-brand 60-page diagnostic took 760 seconds, dominated by Silk timeouts.

**Options considered:**

1. Flat swap to domcontentloaded
2. Timeout fallback: try networkidle with short timeout, on timeout retry with domcontentloaded
3. Different wait strategy: `load` event (intermediate between the two)

**Decision:** Option 1 — flat swap to domcontentloaded + `page.wait_for_timeout(2000)` fixed delay, timeout reduced from 30s to 15s.

**Why:** Option 2 (timeout fallback) would add complexity without meaningful benefit: if networkidle times out for Silk, it will always timeout for Silk, so the fallback would fire on every Silk run anyway. You’d be adding two wait periods per page on the sites that need the fix most. The flat swap is simpler and more predictable. The 2-second fixed delay after domcontentloaded compensates for the main risk of the flat swap (React components rendering after DOM is ready but before JS hydration completes) — it gives the page enough time to hydrate without waiting indefinitely for all network activity to cease. The timeout reduction from 30s to 15s means genuinely unreachable pages fail faster, reducing total runtime for error cases. Result: runtime halved (760s → 342s), all Silk timeout failures eliminated, no regressions on well-behaved sites.

-----

## 2026-06-17 — Per-question max scoring in crawler, entity names removed from query

**Context:** The crawl scoring embedded all questions and entity names together in one blended query string: “sustainability claims plant milk types parent company oatly ripple chobani silk”. Chobani’s /impact page scored 0.459 (below the 0.55 threshold) because “sustainability claims” was diluted by “plant milk types”, “parent company”, and entity names. The page was correctly relevant to sustainability but failed to get followed.

**Options considered:**

1. Keep blended query, lower threshold
2. Per-question embedding, take max cosine as final score (remove entities)
3. Per-question embedding, take max cosine (keep entities)

**Decision:** Option 2 — strip entities entirely, embed each question separately, take max cosine.

**Why for per-question max:** A page relevant to ANY one question should score well. Blending all questions into one vector creates a centroid that’s relevant to none of them clearly. “impact” is semantically close to “sustainability claims” at ~0.65 but that signal disappears when diluted by “plant milk types” and “parent company.” Max-over-questions is the principled fix — it directly implements “this page scores well if it’s relevant to any of our questions.” The cost is modest: 3× the Ollama calls for the question embeddings (3 questions instead of 1 blended), but these are batched.

**Why remove entity names:** Questions define *what kind of information* to look for (topical relevance). Entities define *which company’s pages* to follow (link hygiene). These are orthogonal. “Oatly” in the query drags cosine toward pages that mention the word “Oatly” regardless of their topic — that’s what link hygiene filters do, not what the semantic scorer should do. The semantic scorer’s job is: is this page topically relevant? Entity routing happens separately via domain matching and link anchor text. Conflating them compounds the dilution problem. After the fix: Chobani /impact scored ~0.65+, product pages dropped, the sustainability report page was discovered. Before/after is a concrete measurable improvement suitable for the dissertation evaluation.

-----

## 2026-06-17 — Page-type signal via embedding (INFORMATIONAL_REF / TRANSACTIONAL_REF)

**Context:** Even with per-question max scoring, product pages (scoring 0.60-0.65) sat level with sustainability pages (scoring 0.63-0.65) because both mention “milk” and “plant.” The crawler was consuming its page budget on product pages instead of informational pages.

**Options considered:**

1. URL pattern blocklist (TRANSACTIONAL path segments → penalty)
2. LLM-as-router (one Claude call per entry page to classify all links)
3. Embedding-based page-type signal using reference descriptions
4. Path depth penalty heuristic

**Decision:** Option 3 — `type_score = info_score - trans_score`, applied as `final_score = topic_score * (1 + PAGE_TYPE_ALPHA * type_score)`.

**Why option 3 over option 1 (URL blocklist):** A blocklist of path segments like `/products/` is brittle across domains. A pharma company’s `/products/` page might be their pipeline disclosure — genuinely informational. A hardcoded list encodes domain knowledge that doesn’t generalise. The embedding approach doesn’t need domain-specific rules because it encodes the type of content (about us, sustainability, research, reports vs shop, buy, cart, checkout) in natural language that applies universally across commercial websites.

**Why option 3 over option 2 (LLM router):** The LLM router would generalise better but adds an API call per entry page, cost, and latency. For a consulting-scale pipeline (10–50 brand sites) this is manageable, but it introduces an external dependency and non-determinism. The embedding approach is fully local and deterministic.

**Why multiplicative not additive:** `final_score = topic_score * (1 + alpha * type_score)` means the page-type adjustment scales with the topical relevance. A highly transactional page with genuinely relevant content (rare but possible — a pharma product page containing pipeline data) still has a high topic score, so even multiplied by (1 - 0.4*0.2) it may clear threshold. Additive would let a transactional page compensate with topical relevance and still rank high. Multiplicative preserves the correct priority: topical relevance comes first, page type modulates it.

**Why INFORMATIONAL_REF and TRANSACTIONAL_REF in config.py:** These strings describe universal commercial web vocabulary — “shop, buy, cart, checkout” means the same thing on every commercial website. But putting them in config.py (not hardcoded in link_scorer.py) means an analyst running a pharma engagement can add “pipeline clinical trials efficacy phase” to INFORMATIONAL_REF without touching code. The strings are data, not logic.

**Why PAGE_TYPE_ALPHA = 0.4:** Chosen as a starting point in the range 0.3–0.5. The 0.4 value produces the right direction of improvement (Oatly sustainability pages boost, product pages nudge down) but insufficient separation for Chobani specifically (products/creamers/dairy still slightly outranks /impact). This is documented as a finding for Stage 7 tuning rather than a problem to fix now — the value needs empirical calibration against a ground-truth crawl ranking, which requires Stage 9 first.

-----

## 2026-06-16 — include_links=True in Trafilatura (local backend)

**Context:** The local backend context extraction was producing nav-soup for homepage navigation links. `_discover_links()` has two paths: a markdown path (fires when `](` is in the text, gives ±120 chars of surrounding prose) and an HTML path (fires otherwise, takes the parent element’s text). Trafilatura with `include_links=False` produces plain text with no `](` markers — so the HTML path always fired, grabbing 240 chars of sibling nav items. Every nav link on the Chobani homepage had identical context: “Products Impact About Careers Contact Press.”

**Options considered:**

1. Change Trafilatura to `include_links=True` to preserve markdown links
2. Walk up the DOM tree to find a better block-level parent than the immediate parent
3. Re-fetch HTML separately just for context extraction

**Decision:** Option 1 — single flag change.

**Why:** Option 2 (better DOM walking) requires more code and still fails for cached pages (where HTML is not stored, causing a silent re-fetch via plain requests — itself a waste and producing worse context than the cached version). Option 3 (re-fetch HTML) was already happening silently on cached pages and was confirmed wasteful. Option 1 is elegant: `include_links=True` makes Trafilatura output `[anchor](url)` inline, which means `](` appears in the page text, which means the **markdown path fires** instead of the HTML path. The ±120 chars of surrounding prose is meaningful context — “learn more about our sustainability commitments” rather than “Products About Sustainability Careers Contact Us Press Investors.” Downstream effect: the LLM in Extract sees URLs embedded in the text, which was a concern — but testable. The change also makes local and Firecrawl backends use the same context extraction path (Firecrawl always returns markdown), which is good for consistency and simplifies the codebase. The result was that scoring quality improved for content-rich pages while nav-heavy homepages remained difficult (which is expected — the signal genuinely isn’t there for a nav dump regardless of context quality).

-----

## 2026-06-15 — Plug-in dispatch architecture (ACQUIRE_TOOL / EXTRACT_TOOL in config.py)

**Context:** The pipeline needs to support multiple tools in each layer for both production use and evaluation. The evaluation specifically requires running two configurations (SGAI baseline vs full pipeline) on the same input and scoring both against the same ground truth. If the tools are hardwired into the logic, switching them requires code changes — and any code change could inadvertently change other behaviour, making the comparison unfair.

**Decision:** All tool selection via config.py constants. Dispatch functions (`_get_fetcher`, `_get_extractor`) route to the appropriate implementation. Filter, Verify, Aggregate never know which tools were used upstream.

**Why:** The scientific validity of the evaluation depends on only ONE thing varying between the two pipeline runs: the tools. If the code changes between runs, differences in output could come from the code change rather than the tools. Config-driven dispatch ensures both runs go through identical Filter, Verify, and Aggregate logic — the only variable is the tool at each dispatch point. This is what makes the comparison methodologically sound and publishable. It also makes the pipeline operationally useful beyond the dissertation: an analyst or Nick can change ACQUIRE_TOOL from “firecrawl” to “local” in a single line when running on a corporate network without internet access. The architecture was explicitly grounded in the “separable layers” framing in the interim report — tool swaps are a one-file change, not a code change.

-----

## 2026-06-15 — Separate fetch from extract (SGAI combined call rejected)

**Context:** The original prototype used SGAI’s smartscraper API — a combined fetch+extract call that takes a URL, renders the page with JS, and returns structured JSON answers in one shot. The prototype worked for basic extraction but had a fundamental limitation.

**Options considered:**

1. Keep SGAI combined call as the primary pipeline
2. Save SGAI’s raw content before extraction (if available)
3. Separate fetch and extract into distinct layers with different tools

**Decision:** Option 3 — full layer separation with cached markdown between Acquire and Extract.

**Why:** SGAI’s combined call discards the raw page content before the pipeline receives anything. The API returns structured JSON answers but does not return the source markdown. This makes the Verify layer **architecturally impossible**: Verify needs to check whether the supporting quote actually appears in the source page, which requires the source page. Without saved markdown between Acquire and Extract, you can only trust that the LLM said the quote was there — you can’t check it yourself. This isn’t a prompt engineering problem. No amount of instructing SGAI to “be accurate” changes the fact that the raw page content is gone before you can verify it. Layer separation solves this fundamentally: Acquire saves the page markdown to a SHA256-keyed cache, Extract reads from the cache and returns quotes, Verify checks those quotes against the cached markdown. The quote is either there or it isn’t — no trust in the LLM required. This is the foundational architectural decision that makes the whole pipeline’s reliability claim credible.

-----

## 2026-06-15 — Filter never excludes pages, only routes (completeness guarantee)

**Context:** Designing the Filter layer. The question was whether Filter should be allowed to completely exclude a page from extraction if it deems it irrelevant to all questions.

**Options considered:**

1. Filter can exclude pages (set relevant_columns = empty → page skipped entirely)
2. Filter only routes: if nothing clears threshold, fall back to all columns
3. Filter is a pure passthrough (mark everything relevant)

**Decision:** Option 2 — Filter routes but never excludes. If no question clears either the embedding gate or keyword gate, all questions are marked relevant.

**Why:** The asymmetry of errors makes this clear. A false negative in Filter (a relevant page incorrectly excluded) is unrecoverable — that page never reaches Extract, that answer is permanently lost. A false positive in Filter (an irrelevant page incorrectly included) costs one unnecessary Extract call, but Extract will find nothing and the cell stays empty. The cost of a false positive is wasted LLM credit. The cost of a false negative is a missing answer in the final output. In a consulting context where missing claims is a material reliability concern, that asymmetry justifies the fallback. This also keeps the architecture honest about where completeness responsibility lives: Acquire must find the right pages (completeness lives there), Filter routes efficiently but cannot override Acquire’s decisions. The rule is enforced in code — empty relevant_columns always triggers a fallback to all columns, not a skip.

-----

## 2026-06-15 — Ollama nomic-embed-text replaces sentence-transformers (HuggingFace blocked)

**Context:** The interim report specified sentence-transformers for the Filter layer embedding. The actual implementation discovered that HuggingFace model downloads are blocked by Sagentia IT corporate network policy.

**Options considered:**

1. Request IT exception to allow HuggingFace downloads
2. Bundle the sentence-transformer model files in the repo
3. Use the already-running internal Ollama server

**Decision:** Option 3 — nomic-embed-text via Ollama at `http://10.99.96.1:11434` (768-dim vectors).

**Why:** Option 1 (IT exception) introduces an uncertain timeline on the critical path. Option 2 (bundled model files) is fragile, violates the HuggingFace licence terms for redistribution, and creates a repo with large binary files. Option 3 is the only approach that works within the existing infrastructure without IT involvement. The Ollama server was already running for Paulo’s team, the nomic-embed-text model was already loaded, and the embedding quality is comparable to sentence-transformers for the relevance-scoring use case. Using the same embedding infrastructure across both Acquire (crawl scoring) and Filter (routing) also provides consistency — the same embedding space is used for both relevance judgements, making their relationship interpretable. The BM25 fallback ensures the pipeline continues to function when Ollama is unreachable (when not on the Science Group WiFi or VPN).