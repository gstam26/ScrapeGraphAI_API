# Fable session notes — lessons log

One entry per lesson; one-line summary on top of each. Both corrections and confirmed approaches. Newest first.

---

## Build a null-check into every measurement harness
The filter recalibration re-scored cached pages with the OLD queries alongside the new ones; the old-query AUC reproduced the baseline to three decimals, proving the harness itself was faithful before we trusted the improvement (0.636→0.728). A before/after comparison without that null leg can't distinguish "the fix worked" from "the harness measures differently." (2026-07-03, filter validation.)

## Absolute cosine thresholds don't transfer across embedding anisotropy — calibrate in the deployed geometry
Grouping worked perfectly on orthogonal test vectors and produced one giant blob on real claims at every threshold: claims within a cell share a dominant company/domain component that compresses raw cosines into a narrow band (the same compression seen in filter scores). Fix was per-cell mean-centering, not threshold tuning. Rule: whenever a similarity threshold "works in tests but not on real data," suspect the geometry before the threshold — and calibrate on real data from the deployed domain. (2026-07-03, group calibration.)

## Optimise the metric the error asymmetry demands, not the default one
The threshold sweep's best-F1 operating point for R&D location had recall 0.526 — F1 balances precision and recall, but a filter false negative is an unrecoverable lost answer while a false positive costs one LLM call. The right operating point is recall-first (≥0.95), chosen from the same sweep. Defaults like best-F1 encode an error trade-off; check it matches yours. (2026-07-03, filter thresholds.)

## Audit subagent reports with cheap mechanical checks, not re-reads
The grouping agent reported "11 new tests"; `pytest --collect-only` showed 12 — harmless here, but the same class of error could hide a failure. Counts, SHAs, and table numbers from a subagent are one `--collect-only` / one script re-run away from verified; always spend that minute. Same session: re-ran both diagnostics from the merged main tree rather than trusting the worktree output. (2026-07-03, filter/grouping integration.)

## Parallel worktree agents work well when briefs are prescriptive and file-disjoint
Two agents built the filter fix and the grouping layer simultaneously in isolated worktrees; the only shared file (config.py) auto-merged because each added a distinct section. The briefs specified exact function signatures, cache-key pitfalls, and test cases — the agents' judgment went into implementation, not scope. Confirmed approach for independent multi-file changes. (2026-07-03.)

## Check the execution environment before promising a measurement
The task said "re-measure AUC using the cached validation data" — a 30-second probe showed this machine has neither Ollama nor the ADLM cache (both live on the work laptop). Discovering that BEFORE spawning implementation agents reshaped the deliverable honestly: build + unit-test + before-AUC here, one-command after-AUC script for the laptop, stated as pending rather than asserted. (2026-07-03, filter/grouping build.)

## Match the metric to the question type, or it will lie to you
Cell-level recall ("cell has ≥1 answer") said a 6-page crawl cap kept 92% of quality; item-level recall (distinct claims kept) showed it lost 50% of Diagnostics-type items. The first metric nearly shipped a bad recommendation; the honest metric reversed it. For list questions, always count items, not cells. (2026-07-02→03, crawl-budget analysis.)

## Claim/release discipline: every reserved resource needs a release on every non-completion path
The locale-dedup key was claimed at queue time but never released on fetch failure or threshold re-check skip — permanently blackholing all sibling variants of a page that was never actually acquired. Pattern to check whenever something is "claimed early": enumerate every path where the claimed work doesn't complete, and release on each. (2026-07-03 code review finding #1.)

## Measure before designing — existing logs often already contain the diagnosis
"The filter doesn't work" became "the filter embeds the 3-word column name and discards the 30-word instruction (filter.py:101), AUC 0.64" using only the Filter Log + Provenance from a run that already existed. No new runs, no credits, and the fix became obvious and testable. (2026-07-03, filter diagnosis.)

## Bake-off before trusting a backend — and read the failure signature, not just the rate
playwright_pooled looked complete in code review but failed 57% of pages in measurement. The tell was qualitative: identical 2,539-char extractions on every Bruker page = consent-wall text captured before hydration. A pass-rate alone wouldn't have located the cause; the repeated-constant signature did. (2026-07-02 backend bake-off.)

## Sub-agent findings are candidates, not facts — verify against the code before asserting
The review finders claimed the plant-milk workbook has duplicate-domain rows and that a case-mismatch bug had high impact; direct reading showed the first was unverifiable from this machine and the second low-hit-rate. Report verified facts as facts, the rest as flagged risk. (2026-07-03 code review verification pass.)

## Machine-local mode choices belong in .env, not uncommitted config edits
The work laptop's FILTER_MODE=passthrough edit collided with git pull twice before being moved to an env override. Any per-machine setting that lives as a working-tree edit is a recurring merge conflict with a deadline attached. (2026-07-02→03.)

## Honest capability boundaries beat optimistic promises
Free proxies and stealth anti-bot were requested and declined with reasons (security hazard; indefensible posture); the alternative offered was a measured bake-off with pre-registered thresholds. The bake-off then failed, which is the system working — the refusal + measurement protected the deliverable. (2026-07-02.)
