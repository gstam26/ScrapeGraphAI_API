# Decision Log — AI Extraction Pipeline

**Append-only. Newest entries at top. One entry per architectural decision.**
**Format: Context → Options considered → Decision → Why (complete) → Status/Result**

-----

## 2026-07-21 — Eval framework promoted to src/eval/ (R4 executed) and generalised: GT converter, Matrix mode, matcher validation, cross-encoder experiment

**Context:** George wants the evaluation framework to be the fully generalisable "plug any analyst GT + any pipeline output → metrics" tool the dissertation needs, and asked whether ms-marco-MiniLM-L6-v2 (available locally) can replace cosine similarity. Deferred restructure item R4 (`proposals/code-restructure.md`) was gated on the summary eval no longer being in flight — that shipped 2026-07-15/20, so the gate is open. George gave the go for all of it in one organised pass.

**Built (4 commits, suite 196 → 239, each step green before/after):**
1. **R4 move (behaviour-identical):** `diagnostics/eval_lib/` → `src/eval/` (module names unchanged — they appear in the dissertation text), `generic_eval.py` + `run_eval_suite.py` joined the package, `test_aligner_group_credit.py` → `tests/` (joins the offline suite: 196 → 205). Imports/shims/.gitignore fixture exception updated; `eval_extraction.py` verified to reach its Ollama call on the committed fixtures (off-network hard-fail is its documented contract).
2. **`src/eval/gt_convert.py` — analyst matrix → flat GT.** Closes the biggest generalisation gap: analysts produce matrix-shaped tables, the evaluator reads flat one-row-per-claim GT. Splitting on newlines/semicolons (comma split OPT-IN per column — commas live inside items like "Eden Prairie, MN"), bullet stripping, is_list inference from multi-item cells (`--list`/`--single` overrides; `--single` never splits), null markers → canonical "None (not disclosed)", empty cell = not assessed (no row, deliberately distinct from confirmed-absent). Output round-trips through `read_gt` before success is reported.
3. **`generic_eval.py --sheet matrix` — score the deliverable, not the extraction.** Parses the Matrix cell grammar (bullets, `-- Unverified --` section switch, whole-cell `(unverified)`, conflict/overflow/truncation markers); "No data found" maps to the null sentinel so a correctly-empty deliverable cell scores as a true negative. Items hidden by the display cap count as missing BY DESIGN — the mode measures what the table shows. Validated against the real 07-02 v2 workbook (1,784 claims parsed).
4. **`src/eval/matcher_eval.py` — the matcher's own validation** (the gap ranked first for trustworthiness: 0.65/0.45 thresholds never human-checked). `label-template` writes every matcher decision as labelable pairs (matched = SAME; per missed GT claim its 2 closest leftover AI claims = DIFFERENT); `label-score` reports agreement overall/per-band/confusion vs the pre-registered **0.80 bar** (same bar as the summary judge). George's labelling is the open human step.
5. **Cross-encoder (EXPERIMENTAL, decision deliberately deferred):** `src/eval/cross_encoder.py` + `--semantic-backend cross-encoder` behind a shared scorer abstraction (`_CosineScorer`/`CrossEncoderScorer`: `score/min_score/name`) — default backend behaviour unchanged, existing tests untouched. `filter_recalibration.py --cross-encoder` adds the filter-side A/B: CE AUC + 0.05–0.95 sweep on the same cached 343 pages, directly comparable to the 0.728 embedding baseline.

**Where the cross-encoder is and is not appropriate (analysis, recorded):** it scores PAIRS and produces no vectors — right candidate for filter routing and crawler link scoring (query→document relevance is ms-marco's literal training task, and it sidesteps nomic's anisotropy/score-compression), and for eval matching including LIST cells (where mean-centred nomic had to be disabled — proper nouns collapse onto one axis; a cross-encoder judges each pair independently). Structurally WRONG for group.py clustering (no centroids/mean-centering; HORIBA cell would be 862² pairs) — Ollama stays there. Two caveats in the module header: relevance ≠ equivalence (a paraphrase/STS or NLI cross-encoder is the technically right eval model — pointable via `CROSS_ENCODER_MODEL`), and sigmoid scores share cosine's 0..1 range but NOT its distribution (`CROSS_ENCODER_MIN=0.50` is a placeholder until label-score validates it).

**Not fixable by any of this (inherent):** list-question precision stays a lower bound when GT is non-exhaustive — property of GT collection, honestly reported by the single/list split, not a converter/matcher defect.

**Status:** All shipped offline-tested (239). Open: George labels a matcher template (then `label-score`); CE filter A/B runs on the work laptop (cache + model files there: `--cross-encoder`, no Ollama needed); adoption decisions (filter scorer swap, eval CE backend) wait for those numbers — nothing routes through the cross-encoder by default anywhere.

**CE EVAL-SIDE RESULT (2026-07-21, `ce-rescore` on George's 30 labels, work laptop): CE agreement with the human = 0.967 (29/30) vs production matcher 0.867 (0.90 post numeric fix) — and FLAT across thresholds 0.25–0.70,** so it is not a lucky-threshold artifact. The mirror image of the filter A/B: on ANSWER EQUIVALENCE the cross-encoder clearly beats fuzzy-lexical + centred-cosine; on filter ROUTING it mostly tied. The relevance-vs-equivalence task-mismatch worry did not bite in practice. **DECISION DISCIPLINE: the eval's default matcher is NOT switched yet — switching on the same 30 labels that produced the result would be tuning-to-the-test. Gate: a second label set from another task (task2/task3 output) confirming ≥ the production matcher's agreement → then CE becomes the eval's semantic backend default (keep t=0.50; the plateau makes the choice robust). CE stays available today via `--semantic-backend cross-encoder`** (work laptop only — model files local there; personal laptop falls back visibly to lexical). `print_ce_report` now lists both matchers' disagreement rows so the residual error is inspectable.

**MATCHER HUMAN-VALIDATED (2026-07-21, George's 30 task1 labels — fixture `src/eval/fixtures/matcher_labels_task1_2026-07-21.xlsx`): agreement 0.867, BAR (0.80) PASSED.** Error profile is the valuable part: auto_match band 6/8 correct (over-credits: **"2003" auto-matched "2004"** at 75% token_sort_ratio — a bug class, FIXED same day: pure-numeric values now compare exactly in `_pair_score`, no fuzzy band/semantic rescue, post-fix agreement on this set = 0.90; and "Wikimedia projects" credited for "Wikimedia Commons" — generic-term-for-specific-item over-credit, unfixed, 1 case); review band 0/1 ("Apache Spark" ↔ "Apache License 2.0" — shared-token over-credit); auto_miss 20/21 (the one under-credit: the Wikimedia mission paraphrase the 0.60 semantic floor rejected — evidence the floor is slightly strict, NOT changed on n=1). Caveats: n=30, one task, one labeller. New `matcher_eval.py ce-rescore <labels>` scores the SAME labelled pairs with the cross-encoder (agreement head-to-head + threshold sweep = the CE floor's calibration data) — needs the local model, work-laptop step, OPEN.

**FINAL RESULT (2026-07-21, 3-variant re-run with paired-bootstrap CIs, work laptop, same 141 pages):** ONE replicated significant finding — **CE beats the embedding scorer on Diagnostics type in ALL THREE query forms** (+0.090/+0.108/+0.079, every CI excludes 0; the embedding's weakest question). Everything else is a statistical tie (R&D's apparent CE deficit −0.10..−0.12 straddles 0 on 27 positives; Company type and Recent news well inside noise). The first run's "CE loses 3 of 4" was partly subset noise — honest statement: **tie on 3, CE win on 1**. Query-form finding: instructions HURT the CE (opposite of embeddings); best CE variant = plain name-only (overall 0.745); the natural-question extraction added nothing. **DECISION (George's to revise): filter scorer stays embedding — practical grounds, not statistical:** production filter is passthrough (2026-07-03 quality-first policy), so no swap has any effect today, and adding a torch/sentence-transformers stack buys +0.10 AUC on one non-gating question. **Recorded option if the filter ever gates at 182-scale:** CE with name-only queries for Diagnostics type (or rerun this harness on a full cache first — 141/343 subset caveat stands). Eval-side CE use still open, decided by matcher_eval label-score.

**VERDICT REOPENED (2026-07-21, earlier — George questioned the A/B's rigor, correctly):** two holes in the first CE run: (1) NO significance testing — R&D location has 27 positives on the 141-page subset; ±0.10 AUC deltas there can be noise; (2) ONE query recipe, and the wrong one for the model — MS MARCO is trained on short natural search questions, but the CE got the embedding scorer's `"{name}. {instruction}"` prompt-ese (output-format directives etc.), so the loss may be query-form mismatch, not model capability. Harness extended: CE now scores THREE query forms (name+instruction / name only / natural question auto-extracted as the instruction's first interrogative sentence) and every CE-vs-embedding per-question delta gets a paired-bootstrap 95% CI (resampling pages; `Significant` = CI excludes 0). "No swap" downgraded to "no evidence yet" pending the re-run. Also for the record: the INFORMATIONAL/TRANSACTIONAL dual-reference ("judge") mechanism is CRAWLER-side, not filter-side — the filter A/B correctly excluded it, and a crawl-link-scorer CE A/B remains unbuilt; the keyword-gate OR is likewise outside both legs (known harness property, 2026-07-03).

**CE filter A/B FIRST RESULT (2026-07-21, work laptop, same 141 cached pages both scorers; model = Paulo's local ms-marco folder, offline load — SUPERSEDED by the reopened verdict above, pending 3-variant re-run):** per-question AUC — CE 0.669 / **0.703** / 0.664 / 0.621 vs embedding-new 0.719 / 0.613 / **0.766** / 0.661 (Company/Diagnostics/R&D/News). CE wins ONLY Diagnostics type (+0.09, the embedding's weakest question); loses the other three (−0.05/−0.10/−0.04). The pooled "overall" (CE 0.711 > 0.675) is an artifact of cross-question calibration — CE scores are comparable across questions, cosines carry per-question offsets — and is NOT decision-relevant because the filter operates per-question thresholds. **DECISION: filter scorer stays the instruction-aware embedding; no CE swap, no per-question scorer mix (complexity vs deployability).** Caveats recorded: 141/343 subset (laptop cache no longer holds the full 07-03 validation page set — 202 misses, cause unestablished; A/B internally fair, both legs same subset, but AUCs not comparable to the recorded 343-page 0.728). CE's remaining live use: eval-side matcher, decided by matcher_eval label-score, not this AUC. Two runtime fixes landed en route (4009b5b, e97b3a7): offline-first HF load (TLS interception broke the HEAD check) + load-once-before-loop; CROSS_ENCODER_MODEL now honoured from .env.

-----

## 2026-07-07 — LLM summary layer BUILT (George's green light); SUMMARY_ENABLED stays False until the pre-registered eval bar passes

**Context:** All build gates cleared: George re-reviewed the Azure revision of `brain/proposals/llm-summary-layer.md` and gave the green light (which also carries leadership's Azure-direct sanction, §7 item 3); the §7 work-laptop checklist passed via `diagnostics/azure_test.py` (connectivity/key/deployment/TLS OK, seed-determinism probe `identical outputs: True`, fingerprint `fp_b7c8a4dc64` stable — §5's reduced-nondeterminism assumption CONFIRMED on this deployment). The verified-only base validated clean on 2026-07-06 (pinned replay, Matrix 100/100 identical).

**Built (this session, exactly per the approved design — no design changes):**
- `src/summarize.py` — summarizer over `diag["claim_groups"]` (grouped-scaffolding input), one Azure GPT-4.1-mini call per grouped cell at temperature=0 + `SUMMARY_SEED`, `[C####]`-tagged closed input set (members capped per theme, whole themes never dropped), Tier-1 mechanical gate inline (invented citations / uncited sentences / top-3 theme coverage → fall back to the Digest line, visibly). `system_fingerprint`, exact prompt and raw response recorded per call.
- Claim-ID drift impossible by construction: `src/io_excel.py:build_claim_index` exposes the SAME function the Provenance writer uses, so pipeline-time citations and write-time sheets can't diverge.
- `pipeline.py` — fail-soft hook after grouping, mirroring the GROUPING_ENABLED pattern; config block in `config.py` (`SUMMARY_ENABLED=False` default).
- `src/io_excel.py` — "AI Summary" sheet after Digest (disclaimer in the Summary header; Faithfulness column: `not-assessed` until judged, `fallback (...)` on gate failure); DIAGNOSTICS-gated "Summary Log" audit sheet. Every pre-existing sheet byte-identical with the layer on or off.
- `diagnostics/summary_judge.py` — Tier-2 sentence-level judge (post-run pass, semantic-verify Phase B pattern); judge failure → `not-assessed`, never a pass; uncited sentences flagged mechanically without a call.
- `diagnostics/summary_eval.py` — the judge-validation harness: `positives` (digest lines, faithful by construction; template-arithmetic prefix excluded from judged text), `corruptions` (swap number/entity, inject fact, re-attach citations — judge-scored; delete-top-theme — Tier-1-gate-scored, as designed), `self-agreement`, `label-template`/`label-score` (George's ~50-summary sentence-level labelling).
- Tests: 28 new, all offline/mocked; suite 161 passed.

**Presentation revision (2026-07-07, later same day — George):** the consultant deliverable must be MATRIX-shaped. The AI Summary sheet is now entity rows × question columns with the prose in cells (disclaimer on the Entity header; gate/call-failed cells show the Digest line with a visible `[fallback: ...]` marker + light-orange fill; unsummarized cells "No data found" red, Matrix conventions). The original long format moved into the Summary Log (which gained Faithfulness + Model columns) — nothing was lost, it just stopped being the deliverable. The Tier-2 judge now reads/writes the Summary Log and annotates flagged matrix cells (orange + `[faithfulness: N flagged sentence(s)]` marker); it therefore REQUIRES a DIAGNOSTICS=True workbook — fine, since judging is part of the eval workflow, which needs diagnostics anyway. Eval harness unchanged (it always read the Summary Log). Note this narrows the design's "separate sheet = separate regime" wall only in shape, not substance: Matrix (verified bullets) and AI Summary (cited synthesized prose) remain separate tabs.

**Eval run #1 (2026-07-07, laptop, `outputs/summary_run_2026-07-07.xlsx`) — judge machinery VALIDATED, harness defects found and fixed:** corruptions 52/52 caught (PASS), self-agreement 1.000 (PASS — seeding confirmed end-to-end, single fingerprint `fp_b7c8a4dc64` across all calls). Positives 32/42 (0.762) and judge flagging 17/17 real summaries traced to TWO HARNESS DEFECTS, not judge capability: (1) the positives leg stripped the digest's leading count prefix but left per-theme "(9 items)" counts — the judge correctly called uncited arithmetic unsupported (an under-strip relative to the leg's own documented intent); (2) the naive sentence splitter cut prose and digest lines at abbreviation periods ("Ltd.", "U.S.", "Inc."), creating citation-less fragments that auto-flag in the judge and fail the Tier-1 gate. **Fixes (harness completion, NOT judge/summarizer prompt scaffolding — neither bounded iteration consumed, bar untouched):** `_split_sentences` merges abbreviation fragments (shared by gate + judge); positives judged as ONE unit with ALL arithmetic stripped (`digest_judgeable_text`); judge now persists per-sentence verdict JSON to a "Judge Verdicts" Summary Log column; new `summary_eval.py flags` subcommand prints each flagged sentence next to its cited claims for human diagnosis. Whether the 17/17 real-summary flags were all splitter artefacts or partly genuine over-reach by the summarizer prose is EXACTLY what the re-run + `flags` output decides — if prose genuinely over-reaches, the fix is a summarizer prompt revision (prompt_version bump, summaries regenerate; George's labels are not yet collected so nothing is wasted).

**Eval run #1 DEEP DIVE (2026-07-07, George uploaded the full workbook `summary_run_2026-07-07.xlsx`, 89 summaries, gate pass 17/89):** reading the raw prose against cited claims found the 72/89 gate failures were DOMINATED by a second harness bug + two genuine model habits:
1. **PARSER BUG (mine, dominant): multi-ID citations.** The model writes `[C0183, C0184, C0185]` (many IDs in one bracket) and sometimes chains `[C0183][C0184]`; the single-ID-per-bracket regex `\[(C\d{4,})\]` matched NONE of these, so multi-ID sentences read as uncited. Fixed: `cited_ids()`/`has_citation()` shared helpers match any bracket containing ≥1 ID and expand all IDs inside (summarize.py; judge + eval import them). **Offline re-score on the SAME s1 responses: 17→52/89 gate pass — pure parser fix, zero model change.** New test locks the formats.
2. **GENUINE model habit A — trailing citation dumps:** the model writes several sentences then puts all citations in the last one (e.g. Danaher/Recent news: 4 sentences, one `[C0183, C0184, C0185, C0189, C0244]` at the end → first 3 genuinely uncited). Residual after parser fix: 32 "uncited".
3. **GENUINE model habit B — interpretive/inference sentences:** uncited glosses like Danaher/R&D "These locations indicate the company's presence in… Europe" and the judge-flagged HORIBA/Company-type "produces its own products, **indicating it operates as a manufacturer** [C0525]" where the claim C0525 is literally the tag `own-product`. The judge CORRECTLY flags expansion beyond a terse categorical claim. (Company type / Diagnostics type claims are category TAGS, not prose — summarizing them into sentences inherently over-reaches; noted for George.)
   - **Judge false-positive rate is low but nonzero:** Aalto/Recent-news "will be attending the ADLM Clinical Lab Expo in Anaheim, July 26–30 2026 [C2146]" vs claim "Heading to Anaheim for the ADLM Clinical Lab Expo, July 26–30, 2026" is a faithful paraphrase the judge flagged — a genuine over-strict case. Small volume; George's ~50 labels will quantify it (this is exactly the label-agreement leg's job).

**Scaffolding round 1 SPENT (summarizer prompt s1→s2):** targets habits A+B — mandates inline per-sentence citations ("do not gather all citations into the final sentence") and forbids interpretation/concluding sentences ("no 'this indicates/suggests'"; report terse labels as-is). One bounded round remains. Parser fix is NOT a round (pure bug). Self-agreement 1.000 + corruptions 52/52 stand — no judge-capability problem, so no judge-prompt round spent yet.

**Status / what remains before `SUMMARY_ENABLED=True` in a client-facing config (work laptop, in order):** (1) pipeline run on the 25-sample replay input with `SUMMARY_ENABLED=True` + DIAGNOSTICS → generates summaries; (2) `summary_judge.py` on that workbook; (3) eval legs: `positives` + `corruptions` (combined ≥0.90), `self-agreement` (≥0.90), `label-template` → George labels → `label-score` (≥0.80). Bar held even if the output reads well; below the bar, the Digest stands (fallback ladder §6). Cost ≈ $1–2 per full 178-run + <$5 one-off eval; quota/pricing check from the Azure portal still sizes `SUMMARY_MAX_CONCURRENT_CALLS` (currently a conservative 4).

-----

## 2026-07-06 — FINDING: a populated cache does not pin the crawl — discovery/scoring re-run live every run, and cache hits use a DIFFERENT discovery path than live fetches

**Context:** George's 25-sample re-run (intended to isolate the verified-only output-layer change against the 07-03 baseline) made live Firecrawl calls and selected different subpages per company, despite a fully populated cache — visible via avif scrape failures that only occur on live fetches. The prior advice ("cache intact → identical replay, zero credits") was **wrong**.

**Mechanism (read from `src/acquire/crawler.py`, confirmed against `fetcher.py` and the 07-03 workbook):**
1. The cache persists **page text only** — rendered HTML is discarded (`write_cache(url, text)`, crawler.py:142; cache.py stores one string). Cache hits return `html=None` (crawler.py:137).
2. `_discover_links` dispatches on that: live Firecrawl → anchor-tag parse of **rawHtml** (crawler.py:249-250, the 2026-07-01 nav/footer fix); cache hit → **markdown-regex** parse of cached text (crawler.py:253-254) — the exact link set the rawHtml fix exists to replace. The two paths produce structurally different candidate sets *by design*, not by drift.
3. The markdown path also surfaces **image links** the HTML path cannot: `_MD_LINK_RE` (crawler.py:152) matches the `[alt](url)` inside markdown images `![alt](url)`, and `.avif` is **not** in `_JUNK_EXTS` (crawler.py:92-95, which lists .png/.jpg/.webp/etc. but predates avif). The HTML path reads only `<a href>` tags, so image URLs never become candidates there. This is the smoking gun matching the observed avif failures: those candidates can *only* arise on a cache-served run.
4. **Scoring is never cached and re-runs live every time**: Ollama embedding scoring per run (crawler.py:446-455) with a silent BM25 fallback if Ollama hiccups mid-run; the `>= threshold` select (crawler.py:466), top-30 cap (crawler.py:464), and locale-dedup first-in-batch tie-break (crawler.py:423-425) then amplify any candidate-set or score difference into a different followed set.
5. Every newly selected URL misses the per-URL cache → **live Firecrawl fetch + credits** (crawler.py:141-142). (A third path exists too: cached text with no markdown links at all → discovery re-fetches the page live via `requests`, crawler.py:256-266.)
6. **Baseline provenance, verified:** the 07-03 run's Acquire Log shows 345/345 pages `From Cache=False` — the baseline discovered everything from live rawHtml, the re-run from cached markdown. The page-set divergence is fully explained; no appeal to site changes needed (though those add drift on top for live-vs-live).

**Consequences:**
- **"Same tool, different run ⇒ same pages" is false on two independent axes:** live-vs-cached runs differ *structurally* (discovery-path switch); live-vs-live runs differ *environmentally* (site changes, scorer nondeterminism, mid-run fallbacks). Any A/B comparison that lets both sides crawl is comparing different page sets. This matters for the dissertation's comparative-evaluation methodology: comparisons must **replay a pinned URL list** — the pattern `diagnostics/backend_compare.py` already uses (it re-fetches the baseline Acquire Log's exact URLs) — or explicitly diff page sets before diffing answers.
- Matrix-identical is invalid as a cross-run validation check for output-layer changes. Page-set-independent invariants remain valid: zero unverified Claim IDs/anchors in Grouped Themes; orange flag on every Verified=False Provenance row.

**Candidate fixes — status (updated 2026-07-06, later same day, per George):**
- **(b) DONE:** `.avif .heic .heif .bmp .tif .tiff .jxl .apng` added to `_JUNK_EXTS` (crawler.py); regression test covers the exact failure shape (markdown-image URL becoming a candidate) on both discovery paths.
- **(c) DONE, generalised — now a STANDING REQUIREMENT:** `diagnostics/build_replay_input.py` pins any baseline run's page set into a standard 4-sheet input workbook (Acquire Log "ok"/"cached" pages as depth-0 specs → the pipeline's direct-fetch path, no discovery/scoring; questions+config copied from the original input since the output workbook doesn't store instructions). Every future before/after validation replays a pinned page set via this tool — never re-crawls — so the diff isolates the code change under test. Validated end-to-end against the real 07-03 baseline: 345 pages / 25 entities, round-trips through `read_input`. 4 tests; suite 130.
- **(a) DEFERRED to its own session (George):** persist rawHtml in the cache so cache hits discover from the same DOM as live runs — held deliberately, not mid-validation.
- (d) unprioritised.

-----

## 2026-07-06 — Verified-only grouping/digest enforced (George's standing decision); LLM summary layer designed, not built

**Context:** The 2026-07-05 traceability audit found the "every citation traces to a verified claim" assertion (this log, 2026-07-03) was never enforced — unverified claims appeared as theme anchors and Digest citations. Separately, leadership asked for LLM-synthesized summary prose for consultants.

**Decision (George):** Unverified claims (quotes that couldn't be confirmed against the page) are excluded from grouping/digest/summary; they stay in Provenance flagged for analyst review. This is a standing decision, not per-run tuning.

**Built:** `src/group.py:_display_values` filters grouping input to values with ≥1 verified evidence item ("any-verified": confirmed on one of N pages counts), applied before `GROUP_MIN_ITEMS`; `src/io_excel.py` `claim_index` anchors citations on the first *verified* Provenance occurrence; the orange Verified=False review flag was dead code (string `"FALSE"` vs bool `str()` `"False"`) and now renders. The 2026-07-03 "always a real verified claim" claim is now true by construction. 5 new tests; suite 125 passed. Consequences accepted: Digest/Grouped Themes item counts now count verified claims only (Matrix still shows unverified in its orange sections); synthesized union-list values (no matching evidence — plant-milk columns) no longer group at all.

**Designed only (awaiting George):** `brain/proposals/llm-summary-layer.md` — walled-off GPT-5.5 summary layer over grouped verified claims: own AI Summary sheet, mandatory claim-ID citations, mechanical citation gate + LLM-judge faithfulness eval validated against labelled pairs (digest-lines-as-positives, programmatic corruptions, ~50 human-labelled), pre-registered ship bar, ~1,270 proxy calls per 178-run. Not to be built before sign-off. *(Later revised to Azure GPT-4.1-mini — see the proposal's 2026-07-06 revision.)*

**VALIDATED (2026-07-06, work laptop, George):** pinned replay of the 07-03 baseline (`adlm-inputs/replay_validation_2026-07-03.xlsx` → `outputs/replay_run_2026-07-06b.xlsx`, 2m28s, cache-served): **Matrix 100/100 cells identical to baseline, 0 changed/missing/new; all verified-only invariants PASS** (`diagnostics/validate_verified_only.py`). This simultaneously validates the verified-only enforcement on a real run AND the replay tool's page-set pinning (the same day's accidental crawl run showed 70/100 cells changed — crawl drift, not code). The verified-only base is confirmed clean. Note: an earlier accidental crawl run also surfaced `https://www.bruker.com/<Base64-Image-Removed>` fetched as a page — Firecrawl markdown's inline-image placeholder converted to a URL by markdown-regex discovery; extension-less, so the _JUNK_EXTS fix can't catch it; one more markdown-discovery artifact for the deferred rawHtml-persistence session.

-----

## 2026-07-03 — Traceability chain shipped: claim IDs + Digest sheet + hyperlinks (summarization decision resolved: deterministic)

**Context:** Leadership delegated the summarization call to George; George's requirement: Advisory needs grouping and summary **traceable to claims**, ideally with hyperlinked references. That requirement settles Part 2b of `proposals/filter-and-synthesis.md`: a deterministic template digest is faithful and traceable *by construction*; LLM prose would need a faithfulness eval to earn the same trust. Deterministic chosen; LLM prose stays a written future option.

**Built (io_excel only — pipeline/aggregate/group logic untouched):**
1. **Claim IDs**: Provenance gains a `Claim ID` first column (`C0001…`, sequential in deterministic Provenance order). First occurrence per (entity, question, normalised claim) is the anchor row.
2. **Grouped Themes**: every bullet carries its claim ID (`- claim text [C0042]`), new `Claim IDs` column lists the theme's references, and each Theme cell is an Excel-internal hyperlink to its anchor Provenance row.
3. **NEW Digest sheet** (after Matrix): one template line per grouped cell — `"N items across K themes. Top: “label” (n) [C####]; …"` — assembled mechanically from the theme structure (labels are verbatim member claims). Question cell hyperlinks to the cell's rows in Grouped Themes.
4. **Provenance Source URL cells are real hyperlinks** (capped at 20k rows, well under Excel's ~65k workbook limit) — the final hop to the source page.

**Chain: Digest → Grouped Themes → Provenance → source URL.** Matrix untouched (locked plant-milk metrics safe); the eval pipeline_reader reads Provenance by column NAME, so the added first column is compatible (verified). Claim IDs + URL links work even when grouping is off — Provenance traceability doesn't depend on grouping.

**Verification:** `tests/test_traceability.py` (6 tests) exercises the full chain on a written workbook read back with openpyxl — sequential unique IDs, bullet citations, exact hyperlink targets on Theme/Digest cells, source-URL links, and the no-groups degradation. Suite: **114 passed**.

-----

## 2026-07-03 — Calibration closed: GROUP_SIMILARITY=0.15 (centered); filter recall≥0.95 operating points quantified

**Grouping (centered sweep on real claims, all five big cells):** mean-centering works — real theme structure at every cell (raw was one blob). At **0.15**: HORIBA 862→19 themes, QuidelOrtho 10, Hologic 6, Aniara 8, Monobind 6 — all in the scannable range. The provisional 0.30 would have fragmented HORIBA into 93 clusters. Set `GROUP_SIMILARITY = 0.15`; 0.10 noted as the tighter alternative (5–12 themes). **Remaining human step before the sheet goes in a client deliverable: sanity-read theme coherence** (cluster counts can't prove members belong together; the sheet is additive/optional so this is a review gate, not a launch blocker).

**Filter (full sweep, NEW queries, recall-first per the 2026-06-15 asymmetry):** highest threshold keeping recall ≥ 0.95, with the extraction-call saving it buys (score-only; the keyword-gate OR adds recall on top):

| Question | threshold | precision | recall | calls skipped |
|---|---|---|---|---|
| Company type | 0.63 | 0.517 | 0.968 | 14.3% |
| Diagnostics type | 0.61 | 0.536 | 0.977 | 7.0% |
| R&D location | 0.58 | 0.109 | 0.974 | **1.0%** |
| Recent news | 0.58 | 0.400 | 0.967 | 14.0% |

**Honest reading:** the fix made the filter *measurably work* (AUC 0.728), but at quality-safe recall it saves only ~7–14% of extraction calls on three questions and ~nothing on R&D location — confirming "never gate R&D location" and keeping **passthrough as the right mode while quality is the priority**. The likely bigger payoff of instruction-aware queries is the crawl-side link scorer (better page selection with the same budget) — not yet re-measured; needs a sample crawl re-run when fetch credits/backend allow.

-----

## 2026-07-03 — Work-laptop measurements: filter fix VALIDATED (AUC 0.636→0.728); raw-cosine grouping REFUTED → mean-centering added

**Filter (validated):** `filter_recalibration.py` on all 343 cached validation pages. Harness self-check passed — re-scored name-only queries reproduce the baseline AUC to 3 decimals (0.636), so the measurement is trustworthy. New name+instruction queries: **overall AUC 0.636 → 0.728**; per-question 0.607→0.746 (Company type), 0.547→0.719 (Diagnostics), 0.623→**0.792** (R&D location), 0.745→0.756 (Recent news). Diagnosis confirmed end-to-end.
**Threshold decision — do NOT adopt the best-F1 thresholds:** best-F1 optimises the wrong objective. A filter false negative is an unrecoverable lost answer (2026-06-15 asymmetry), and at best-F1 the R&D-location operating point has recall 0.526 — it would discard ~47% of the starved question's answer-pages to save extraction calls we aren't currently paying for. Also the sweep understates production recall (routing is score ≥ t OR keyword-gate; the sweep measures score alone). **Policy: stay passthrough while runs are validation-scale; when cost matters again (paid 182-scale runs), pick per-question thresholds at the recall ≥ 0.95 operating point from the full sweep in `adlm-outputs/filter_recalibration.xlsx`, and consider never gating R&D location.**

**Grouping (refuted, fixed):** `group_calibration.py` on real claims — at every threshold ≤ 0.70 the 862-claim HORIBA cell stays ONE cluster (816/862 even at 0.70; 668+104+fragments at 0.75, still not themes). Root cause: claims within a cell share a dominant company/domain embedding component; raw cosines compress into a narrow high band (same anisotropy family as the filter-score compression). Fix (commit f20a82a): per-cell mean-centering (`center_vector_map`, All-but-the-Top style, deterministic) behind `GROUP_CENTER_VECTORS=True`; `GROUP_SIMILARITY` → 0.30 provisional in centered space; calibration script now sweeps RAW and CENTERED so one re-run picks the final value; the one-blob failure geometry is reproduced in a test and centering's separation proven. **Pending: one work-laptop re-run of `group_calibration.py` + a theme-coherence sanity read before trusting the sheet.**

-----

## 2026-07-03 — Filter routes on name+instruction; deterministic Grouped Themes layer added (leadership's quality pivot)

**Context:** Leadership redirected: no big batch runs (credits out), focus extraction quality — make Filter work, add grouping/summarization at Aggregate. Diagnosis already on record (`proposals/filter-and-synthesis.md`): Filter embedded only the 2–3 word column NAME as its query (`filter.py`), discarding the 30–50 word instruction; measured score-vs-answered AUC on the validation run was 0.64 overall (0.55–0.74 per question) — why passthrough was on. Twin defect in the crawl link-scorer call sites.

**Decision (two independent changes, built by parallel worktree agents, merged + audited):**
1. **Instruction-aware routing:** shared `query_text(col)` helper → `"{name}. {instruction}"`; used by Filter's `score_page_columns` AND both crawler embed-scorer call sites. `QUERY_INCLUDES_INSTRUCTION = True` config flag (False restores name-only for A/B). Critical detail: `_question_emb_cache` key changed from name-tuple to query-text-tuple (else stale name-only embeddings would be silently reused). Keyword gate stays on the name (instruction words are generic — would over-fire). BM25 `build_crawl_query` untouched (already instruction-weighted); experimental scorer untouched (already handles instructions).
2. **Grouped Themes (proposal Part 2a only — 2b LLM summarization NOT built, per George):** new `src/group.py` — deterministic greedy clustering of each aggregated cell's display values (sorted iteration, first-match centroid ≥ `GROUP_SIMILARITY=0.62`, incremental centroids; medoid member string as theme label — always a real verified claim, never synthesized). One `embed_batch` per run. Cells < `GROUP_MIN_ITEMS=6` → single "(all items)" group with zero embedding calls. Output: "Grouped Themes" sheet (Entity | Question | Theme | Items | Values | Distinct Sources), written after Provenance, reusing the display-cap + clamp conventions. Matrix/Provenance/aggregate.py untouched → locked plant-milk metrics safe. Pipeline hook is try/except with `GROUPING_ENABLED`; Ollama-unreachable or any failure → one printed line, sheet absent, run unaffected.

**Verification (audited against actual output, not agent reports):** suite 106 passed on the merged tree (90 base + 4 filter + 12 grouping; one agent under-reported its own test count by 1 — caught by `--collect-only`). BEFORE AUC table reproduced from my tree: 0.607/0.547/0.623/0.745, overall 0.636. Both diagnostics (`filter_recalibration.py`, `group_calibration.py`) confirmed to degrade gracefully off-network (exit 0, clear work-laptop instructions).

**Pending (work laptop — Ollama + validation cache live there, cannot be computed off-network):** AFTER AUC table + threshold sweep (`python diagnostics/filter_recalibration.py`), and `GROUP_SIMILARITY` calibration on real claims (`python diagnostics/group_calibration.py`). Until the AFTER table shows real separation, `FILTER_MODE` stays passthrough — the fix changes what scores are computed, not yet what routes.

**Status:** Merged and pushed. LLM summarization remains a written option only (`proposals/filter-and-synthesis.md` Part 2b) — awaiting George's deterministic-template vs LLM decision.

**Context:** Full high-effort review (8 finder angles, candidates verified by direct code reading) of everything from 2026-07-02: entity parallelism, crawl locale-dedup/score-aware cap, LLMAPI retry, output limits, playwright_pooled backend. 10 real findings surfaced; George approved fixing the top 2 before batch 1, recording the rest.

**Fixed:**
1. **Locale-key permanent blackhole** (`src/acquire/crawler.py`): a locale key was claimed at queue time and never released if the claimed URL then failed to fetch (exception) or got skipped by the depth>0 threshold re-check — any later-discovered sibling sharing that key was silently dropped as a "duplicate" of a page that was never actually acquired, undoing some of the locale-dedup fix's own recall gain. Fixed: both failure points now `visited_locale_keys.discard(_locale_key(current.url))` before continuing. Single-threaded per-entity crawl loop, so no race on the release. Test: `test_locale_key_released_after_fetch_failure`.
2. **Whole-run crash from one bad print()** (`pipeline.py`): `_process_url_spec`'s identifying print sat outside its try block; a workbook entity name or URL with a character outside the console's codepage (realistic on Windows, e.g. accented company names) raises `UnicodeEncodeError` there, which propagated through the unguarded `future.result()` in `run_pipeline` and would have discarded every already-completed entity's results. Fixed two ways: (a) new `_safe_print()` helper (encode/replace/decode fallback, never raises) used for every print in `_process_url_spec` that embeds workbook-controlled strings; (b) the entity-level future collection now has the same try/except backstop the page-level pool already had (defense in depth — catches *any* per-spec exception, not just this one). Also removed the now-redundant serial-loop branch (`max_spec_workers <= 1`) while fixing this, since it duplicated the pool's behavior and was the exact kind of "fix one branch, forget the other" trap this bug came from. Tests: `test_safe_print_survives_encoding_error`, `test_safe_print_passthrough_for_plain_text`, `test_run_pipeline_survives_one_spec_crashing`.

**Recorded, not fixed (in severity order — revisit before or during the 178-company run if time allows):**
3. Score-aware cap removed the pre-scoring truncation, so `score_links_embed`/`_experimental` now embed every same-domain candidate on a page (previously ≤30) before the cap applies — nav-heavy/archive pages can push 300+ texts into one Ollama batch call, risking `OLLAMA_TIMEOUT` and a mid-crawl silent fallback to BM25 (which re-normalises scores per-batch, changing follow/skip decisions).
4. `LLMAPI`'s 5xx retry sleeps 5s while still holding one of the 16 global `_LLM_CALL_SEMAPHORE` slots — during a proxy brownout (the exact condition it targets), correlated 502s across chunks can occupy most/all slots in blocking sleep, collapsing throughput instead of gracefully degrading.
5. `EXTRACT_MAX_CHUNKS_PER_PAGE` truncation is `print()`-only; the Extract Log's `page_length_input` still shows the full untruncated length with no truncated/chunks-used flag — contradicts this feature's own "never silent" rationale, since the one artifact meant to make it visible (the diagnostic workbook) doesn't.
6. `write_cache` is non-atomic (`open(path,"w")` truncates before writing) and entity-level parallelism has no enforcement of its own "one spec = one seed domain" assumption — two specs resolving an overlapping URL could race the cache. Design risk, not yet observed.
7. The politeness gate (per-domain delay, robots.txt) lives only in `playwright_pool.py`, wired to `playwright_pooled`; the `local`/`requests`/plain `playwright` backends remain fully unthrottled despite also being self-hosted (fetching from this machine's IP).
8. `_process_url_spec` sets `result["error"]` on exception but `run_pipeline`'s merge loop never reads it — a crashed spec renders identically to a legitimately-empty one in the Matrix/Summary, with no artifact flagging which companies need a re-run.
9. `robots_allows()`'s `RobotFileParser.read()` has no timeout — an unresponsive robots.txt endpoint hangs the calling thread forever. Only reachable via `playwright_pooled`, which is not yet in production use (still pending the bake-off re-fix), so lower urgency than 3–8 for now but must be fixed before that backend ships.
10. `_locale_key`/`_same_domain` (crawler.py) and `playwright_pool._domain` normalise the netloc three subtly different ways (case-sensitivity differs) — low practical hit-rate since `urljoin`-derived URLs are rarely mixed-case, but worth consolidating into one shared host-normalisation helper if the code gets touched again.

**Status:** #1–2 applied, 90 offline tests pass (was 86). #3–10 recorded for the post-batch-1 cleanup pass.

**Context:** v2 validation run, HORIBA "Recent news": 957 raw evidence rows (654 from ONE page), 5m37s extract, Excel cell-length warning. Suspicion: evidence duplicating across pages / a regression from the entity-parallelism change. Blocks batch 1.

**Diagnosis (from the v2 workbook):** NOT duplication and NOT parallelism. 862 of 957 claims are distinct (~10% dup rate, in line with other entities: QuidelOrtho 9%, McKesson 5%); dedup and aggregation behaved correctly. Root cause chain: `/usa/company/news` is a **735 KB news-archive page** scoring 0.748 on the news question → followed because the same-day **score-aware cap fix** let it into the pool (v1's DOM-order cap had excluded it by accident, not by design) → 95 chunks → 95 LLM calls (1,361 s summed) → 654 legitimately distinct news items → Matrix cell hit Excel's 32,767-char hard limit and was silently truncated (cell measured exactly 32,767). Two real bugs at the boundaries: unbounded per-page extraction cost, unbounded Matrix cell size.

**Decision (3 explicit bounds, nothing silent):**
1. `EXTRACT_MAX_CHUNKS_PER_PAGE = 40` (~312 KB) — cap with a printed warning; archives list newest first so the kept prefix is the "recent" content. Plant-milk maximum is 15 chunks (Oatly 113 KB) → **locked benchmark unaffected**.
2. `MATRIX_MAX_DISPLAY_ITEMS = 50` — cells render at most 50 bullets (verified kept preferentially) + `[+N more items — see Provenance]`. Provenance keeps everything.
3. `_clamp_cell_text` in io_excel — hard clamp below 32,767 on a line boundary + `[truncated — full list in Provenance]`, replacing openpyxl's silent truncation.

**Rejected:** capping evidence at aggregation (would gut the audit trail); skipping oversized pages entirely (their head is exactly the recent-news content Q4 wants).

**Status:** Applied; `tests/test_output_limits.py` (6 tests, incl. locked-benchmark-scale non-regression). Suite 86 green. Batch 1 unblocked.

-----

## 2026-07-02 — playwright_pooled backend built (politeness gate mandatory); free proxies and stealth anti-bot REJECTED

**Context:** Firecrawl credits remaining: 1,025 ≈ 74 of 178 companies at the measured 13.7 pages/entity. No budget for top-ups without leadership. The remaining ~108 companies therefore need a free fetch path — the self-hosted backend from `proposals/firecrawl-replacement.md`, promoted from "worth testing" to "required to finish".

**Options considered:**
1. Squeeze 178 into 1,025 credits by cutting `CRAWL_MAX_PAGES` to ~5 — REJECTED: would gut the About/locations coverage that just fixed Q1 (validated same day).
2. Free proxy lists for IP hiding — REJECTED: unreliable, and routing company traffic through unknown third-party proxies is a security hazard (MITM) worse than the problem it hides.
3. Stealth anti-bot evasion plugins — REJECTED: indefensible posture for a dissertation/consultancy tool; hard-blocked sites are recorded as findings (same treatment as Firecrawl's 5/60 plant-milk failures).
4. Pooled Playwright + Trafilatura + mandatory politeness gate — CHOSEN.

**Decision:** `ACQUIRE_TOOL="playwright_pooled"` (`src/acquire/playwright_pool.py`): thread-local persistent Chromium (sync API is not cross-thread-safe; one browser per pipeline worker; kills the ~1–2 s per-page launch cost), Trafilatura text, full 3-rule quality gate, rendered DOM into link discovery (nav links present by construction — no rawHtml workaround needed). Politeness gate built-in, not optional: per-domain ≥`CRAWL_POLITE_DELAY_S`=2 s across all threads, robots.txt per-domain cached (disallowed → skipped with `robots_disallowed` provenance; unreadable → allow), honest UA. Off by default.

**Status:** Built + offline-tested (8 tests, no browser/network). NOT yet pointed at external sites — usage (not code) awaits leadership's IP-exposure sign-off. Go/no-go = the pre-registered bake-off in the proposal: re-fetch ~5 batch-1 companies, compare pages/cells/failures vs Firecrawl. Batch slicing added to `build_182_workbook.py` (`--start/--end`) so batch 1 (1–70, Firecrawl) can run meanwhile.

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

**Decision:** All tool selection via config.py constants. Dispatch is the `_FETCHERS` dict + backend branches in `fetch_page_with_provenance` (src/acquire/fetcher.py) and the extract-tool if/elif chain in `extract_cells` (src/extract.py). *(Corrected 2026-07-02: originally written as `_get_fetcher`/`_get_extractor` functions, which never existed under those names.)* Filter, Verify, Aggregate never know which tools were used upstream.

**Why:** The scientific validity of the evaluation depends on only ONE thing varying between the two pipeline runs: the tools. If the code changes between runs, differences in output could come from the code change rather than the tools. Config-driven dispatch ensures both runs go through identical Filter, Verify, and Aggregate logic — the only variable is the tool at each dispatch point. This is what makes the comparison methodologically sound and publishable. It also makes the pipeline operationally useful beyond the dissertation: an analyst or leadership can change ACQUIRE_TOOL from “firecrawl” to “local” in a single line when running on a corporate network without internet access. The architecture was explicitly grounded in the “separable layers” framing in the interim report — tool swaps are a one-file change, not a code change.

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

**Why:** Option 1 (IT exception) introduces an uncertain timeline on the critical path. Option 2 (bundled model files) is fragile, violates the HuggingFace licence terms for redistribution, and creates a repo with large binary files. Option 3 is the only approach that works within the existing infrastructure without IT involvement. The Ollama server was already running for Paulo’s team, the nomic-embed-text model was already loaded, and the embedding quality is comparable to sentence-transformers for the relevance-scoring use case. Using the same embedding infrastructure across both Acquire (crawl scoring) and Filter (routing) also provides consistency — the same embedding space is used for both relevance judgements, making their relationship interpretable. When Ollama is unreachable (not on Science Group WiFi/VPN) each layer degrades independently: Acquire falls back to BM25 link scoring, Filter routes all columns, Verify skips the semantic score. *(Corrected 2026-07-02: the BM25 fallback is crawler-only, not pipeline-wide.)*