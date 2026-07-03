# Proposal: Make Filter work + a Synthesis layer at/after Aggregate

**Status:** Design + evidence, 2026-07-03. Prompted by Nick: focus on extraction quality, not scale (credits out); make filtering work; explore a grouping/summarization layer at aggregate "or the stage after that." No code changed yet.

**Bottom line:** Both are worth doing, both are buildable and validatable WITHOUT Firecrawl credits (cached pages + local Ollama), and both generalize to the plant-milk task. But the summarization half carries a hard constraint the current architecture was built to protect — verifiability + determinism — so it must be a layer STRICTLY OUTSIDE the verified chain, evaluated on its own faithfulness metric, never a replacement of the Matrix. Getting that boundary right is the whole game.

---

## Part 1 — Making Filter work (evidence-based)

### What's actually wrong (measured on the 25-company validation run)

The Filter routes each page to the questions it can answer via `max chunk cosine(page, question) >= FILTER_THRESHOLD`. It's on `passthrough` because the scorer doesn't discriminate on ADLM. Quantified, using "did this page actually produce a verified claim for this question?" as a weak label over the validation Filter Log (1,372 page×question rows) vs Provenance:

| Question | AUC (score vs answered) |
|---|---|
| Recent news | 0.74 |
| R&D location | 0.62 |
| Company type | 0.61 |
| Diagnostics type | 0.55 |
| **Overall** | **0.64** |

0.5 = useless, 1.0 = perfect. Scores are crammed into 0.40–0.70 (median 0.555) — the known nomic-embed compression. **Root cause (confirmed in `src/filter.py:101`):** the filter embeds only the column *name* — the 2–3 word label "R&D location" — and throws away the instruction. The instruction is a 30–50 word discriminative probe: *"In which country does the company conduct its R&D? Check headquarters, locations, laboratories, or about pages."* "Recent news" scores best (0.74) precisely because its label happens to contain distinctive words; the others are generic.

Note: the crawl link-scorer (`score_links_embed`) has the *same* defect — it embeds `[col.name]` only (`link_scorer.py`). So this is one systemic lever, not two.

### The fix (buildable + validatable now, zero credits)

1. **Route on name + instruction, not name alone.** Build the query embedding from `f"{col.name}. {col.instruction}"` (or embed both and take the max). Richer probe → wider score separation.
2. **Re-calibrate the threshold** on the validation Filter-Log distribution once scores actually separate, per-question if needed (Recent news already works; Diagnostics is hardest).
3. **Validation with no credits:** the 343 validation pages are already cached and the Filter Log already has the name-based scores. On the work laptop (Ollama reachable), re-score those same cached pages with instruction-aware queries, recompute the AUC table above, and only retire `passthrough` if AUC climbs enough that a threshold beats "route everything." Pure local embeddings — no Firecrawl, no LLM.

**Why this serves Nick's goals directly:** a filter that discriminates means fewer page×question extraction calls (the HORIBA news archive would never route to "R&D location"), which cuts LLM cost AND cuts the noise that lands in Aggregate. Filter quality and Aggregate quality are the same problem from two ends.

**Plant-milk generalization / locked-metrics safety:** plant-milk questions carry instructions too, so the fix helps there. It does NOT touch the locked RQ1 extraction eval — that ran on hand-picked depth-0 URLs under passthrough, so filter routing was already bypassed there by construction. The change only affects production/crawl routing. Safe.

---

## Part 2 — Synthesis layer (grouping + summarization) at/after Aggregate

Nick conflated three things under "filter/grouping/summarization." They are NOT equally safe — separating them is the key move:

### 2a. Grouping — deterministic, safe, build first

Cluster the verified claims *within* a cell into themes (HORIBA's ~600 news items → "regulatory clearances", "product launches", "partnerships"; Oatly's sustainability claims → "emissions", "packaging", "sourcing"). Mechanism: embed each claim (Ollama, already available), cluster by cosine similarity (deterministic with a fixed threshold — no LLM, reproducible). **Every claim stays verified and traceable; grouping only reorganizes.** Output as a NEW sheet ("Themes"/"Grouped Matrix"), leaving the Matrix untouched so the locked plant-milk F1 (scored on Matrix claim strings) is unaffected. This alone fixes the "600 raw items is useless to a consultant" problem without any of the risk below.

### 2b. Summarization — LLM, risky, needs a guardrail decision

Synthesizing prose ("In 2026 HORIBA announced ~15 product launches and 8 regulatory clearances…") is where the architecture pushes back hard, and I won't quietly build past it:

- **Verifiability:** the pipeline's core trust claim is *every* output value has a verbatim source quote we verified. A summary is synthesized text with no single source span — it structurally cannot be verified the same way. Drop it into the Matrix and the reliability guarantee dies.
- **Determinism:** the only on-network LLM is the Power Automate proxy, which exposes no temperature/seed (`{"text": prompt}` only). Same input → possibly different summary. The **2026-06-24 decision explicitly rejected** injecting a non-deterministic LLM into the verified→scored chain for exactly this reason ("running it twice on the same input could produce different verified claim sets… breaks the trust the dissertation rests on"). Summarization revives that tension one layer up.
- **Locked metrics:** if a summary ever replaces a Matrix cell, plant-milk F1 is invalidated.

**The reconciliation that makes it defensible:** a Synthesis layer is acceptable ONLY if it sits STRICTLY OUTSIDE the verified chain — a presentation pass over already-verified, already-scored claims, that (i) never feeds back into verification/scoring, (ii) cites the verified claim IDs each summary sentence draws from (traceability preserved even though the prose is synthesized), (iii) is clearly marked "AI-synthesized — see Matrix/Provenance for verified claims," and (iv) is evaluated on a NEW **faithfulness** metric (does the summary assert only things present in the cited verified claims? — measurable, and itself a Stage-10-style contribution), NOT on the locked claim-level F1. Framed that way it strengthens the "separable layers + evaluation framework" thesis (an optional, tool-swappable Synthesis layer: deterministic grouping vs LLM summary, separately evaluable) instead of undermining it.

**Generalization:** grouping/summarizing works identically on Oatly sustainability claims and HORIBA news — it's domain-general, which is what makes it a real contribution rather than an ADLM patch.

---

## Sequence & what needs a decision

**Buildable + validatable now, no credits, low risk:**
1. Filter instruction-aware routing + re-calibration (also fix the twin defect in the crawl scorer). Validate on cached validation pages via local Ollama.
2. Deterministic grouping as a new output sheet.

**Needs Nick's decision before building:**
3. The Summarization layer — specifically: (a) is a non-deterministic, separately-evaluated, clearly-marked synthesis pass acceptable given 2026-06-24, or do we keep synthesis deterministic-only (grouping + template-filled counts, no free-text LLM)? (b) does it ship in the client-facing workbook or stay a diagnostic? A deterministic template summary ("N launches, M clearances, K partnerships" built from the groups in 2a) captures most of the consultant value with none of the verifiability/determinism cost — that may be the smart stopping point.

**Decision for George/Nick:** approve building 1 + 2 now (safe, credit-free, immediately useful), and rule on whether Part 2b is deterministic-template-only or an LLM synthesis pass with the faithfulness-eval guardrail.
