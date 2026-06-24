# Report Deltas — Where Reality Diverges from the Interim Report

**Keyed to interim report sections. Each entry = something to correct or expand in the dissertation.**

-----

## §5.2 System Architecture — Extract Layer tool

**Report says:** Claude API, Sonnet 4.6 as primary extractor (“Primary: Claude API, Sonnet 4.6 — fed only the cached markdown; strongest extractor”)

**Reality:** GPT-5.5 via Power Automate proxy (`src/llmapi.py`, `LLMAPI.call(text)`). Direct Claude/OpenAI API calls are blocked by Sagentia IT web policy. Power Automate flow is the approved workaround. LLMAPI is the production interface.

**Dissertation action:** Correct Table 5.1. Adds enterprise-constraint narrative (RQ2 — operational feasibility within Microsoft 365 ecosystem). Actually strengthens the RQ2 contribution — the Power Automate workaround IS the enterprise integration story.

-----

## §5.2 System Architecture — Filter layer tool

**Report says:** “sentence-transformers — embedding similarity + keyword gates (free, pip install, runs locally)”

**Reality:** Ollama `nomic-embed-text` (768-dim) at `http://10.99.96.1:11434`. HuggingFace model downloads are blocked by Sagentia IT. Keyword gate implemented as described. Shared via `src/embed.py`.

**Dissertation action:** Correct Table 5.1. Update infrastructure constraints narrative. Note the Ollama dependency and network-reachability requirement (Science Group WiFi or VPN).

-----

## §5.2 System Architecture — Verify layer tool

**Report says:** “MVP: rapidfuzz — exact substring then fuzzy partial-ratio fallback. Final: NLI entailment model via HuggingFace. Stretch: LLM-as-judge”

**Reality:** rapidfuzz is implemented and working. NLI via HuggingFace is likely blocked (unconfirmed — HuggingFace downloads blocked by IT). LLM-as-judge was designed but not built (rejected as premature given current metrics). A recorded `semantic_score` (cosine of claim vs quote via Ollama) has been added as a diagnostic signal — NOT a gate.

**Dissertation action:** Note NLI blocked, LLM-as-judge deferred as future work. Document semantic_score as a novel addition not in the original plan. The entailment gap (verified = quote exists, not = quote supports claim) should be explicitly discussed as a known limitation.

-----

## §5.4 Stage 10 — Automated testing pipeline

**Report says:** Planned for Weeks 8–11. “Designed to run incrementally as the pipeline is refined, and reusable beyond the internship.”

**Reality:** Substantially complete. Built five modules: `gt_reader.py`, `pipeline_reader.py`, `aligner.py`, `metrics.py`, `report_writer.py` (pending). Evaluated against 86-claim analyst-built ground truth (10 entities, 3 questions, plant milk domain).

**Key results to incorporate:**

- Overall recall: 0.89–0.93 (auto → full after manual review)
- Sustainability recall: 0.66–0.80 (the hard extraction task)
- MilkTypes + ParentCompany recall: ~1.00
- Precision: 0.73 strict / 0.74 distinct (tiny gap — redundancy not the main issue)
- Hallucination: 0 (after null-sentinel reclassification)
- Matched-pair cosine: 0.94
- Tag-slice finding: pipeline recovers achievements/targets at ~1.00, collapses on commitments (0.14), disclosures (0.00)

**Dissertation action:** This is the core RQ3 contribution. Write up the framework design (7 sections: matching algorithm, metric definitions, null handling, list vs single-answer, output design, semi-automated boundary, edge cases), the methodological decisions (greedy 1:1 + quote_id exception, token_sort_ratio dedup, dual precision), and the results with the tag-slice analysis.

-----

## §5.3 Crawl Depth as Design Variable (RQ4)

**Report says:** Stage 7, Weeks 6–8. “Empirically test crawl depth at 0, 1, and 2 on a representative set of brand sites.”

**Reality:** Partial. Five-backend comparison run (the main Stage 7 contribution for Acquire). Crawl-depth evaluation (depth 0 vs 1 vs 2 on same URL set) not yet run as a separate controlled experiment. The extraction evaluation used depth-0 on exact GT URLs (deliberately, to isolate extraction from acquisition).

**Dissertation action:** Frame what was done: backend comparison IS the acquisition-quality evaluation. Frame what wasn’t: systematic depth 0/1/2 comparison on the same URL set is still outstanding as the “completeness delta” metric. This can be scoped as a future work item or a limited experiment.

-----

## §5.5 Evaluation Metrics — sub-page localisation

**Report says:** “Sub-page localisation accuracy — exact-match rate of the recorded character span; spot-checked deep-link resolution”

**Reality:** `char_span` is computed and stored on `SourceQuote` by the Verify layer. However, `char_span` is NOT written to the Excel Provenance sheet by `io_excel.py`. Therefore sub-page localisation (metric 2.7) cannot be scored from the workbook — it was omitted from the Stage 10 evaluation.

**Dissertation action:** Note as known limitation. State char_span is computed at the data-model level but not yet surfaced in the output schema. Fix is a new Provenance column (one io_excel.py change) — could be added before submission.

-----

## §3.5.2 Dedicated extraction tools — SGAI status

**Report says:** “ScrapeGraphAI — allows extraction schemas to be specified through natural-language prompts. Trade-off: Firecrawl offers strong retrieval robustness… ScrapeGraphAI’s permissive MIT licence and modular Python architecture make it a more practical foundation for a self-hosted internal pipeline.”

**Reality:** SGAI managed API failed completely as a fetcher (0/60 pages, API errors on all test entities). Retained as extraction baseline for evaluation comparison. The “more practical foundation” claim proved incorrect for the fetching use case.

**Dissertation action:** Update §3.5.2 and §4.4 findings. The discovery study finding that “ScrapeGraphAI’s default fetching layer returned navigation and footer material rather than substantive content” was correct — the managed API failed even more severely. This strengthens the argument for layer separation (fetch vs extract decoupled).

-----

## §4.7 Current Prototype Status

**Report says:** Prototype built around ScrapeGraphAI. “operates one URL per row and one topic per URL: there is no matrix extraction model, no ability to combine information from multiple URLs into a single cell, and no subpage crawling.”

**Reality:** Full four-layer pipeline now built and producing end-to-end output. Matrix model, multi-source aggregation, subpage crawling, Filter routing, Verify with verbatim quotes — all implemented. Extraction LLM is GPT-5.5 via Power Automate, not SGAI.

**Dissertation action:** Chapter on implementation (Stage 6 complete). This is the main narrative of the dissertation’s technical contribution — the progression from prototype to full pipeline.

-----

## §1.2 Industrial Context — deployment and tools

**Report says:** “Microsoft 365 enterprise ecosystem, so a deployable solution is expected to integrate with existing tooling and to be usable by non-technical consultants”

**Reality:** The Power Automate integration IS the Microsoft 365 integration story. LLMAPI routes through Power Automate flow, which is native M365. This is more concrete than the generic statement in §1.2.

**Dissertation action:** Strengthen §1.2 and RQ2 discussion with the Power Automate evidence. The constraint (blocked APIs) produced a solution (Power Automate workaround) that directly addresses the deployment requirement.