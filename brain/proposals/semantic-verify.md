# Proposal: Semantic verification — closing the "quote exists ≠ quote supports" gap

**Status:** Investigation complete (2026-07-02). No code changed. Recommendation: **two-phase design — a free deterministic tier now, an LLM-judge tier only after it passes its own evaluation.** Never a silent gate.

## What exists today (read from code, not docs)

- The gate: `_verify_quote` (`src/verify.py:24-45`) — exact substring → normalised `partial_ratio ≥ 70` → soft anchor path for long quotes. Answers "does the quote appear on the page", nothing about support.
- **Already half-built:** `verify_cells` (`src/verify.py:98-137`) computes `semantic_score = cosine(value_embedding, quote_embedding)` via the internal Ollama server for every quoted evidence item, batched, and stores it on `SourceQuote.semantic_score` + the Verify Log. It is diagnostic-only — it gates nothing. Report-deltas §5.2 already describes it as "NOT a gate".
- Aggregate already ranks evidence by `semantic_score` (`src/aggregate.py:91-100`), so the score influences which evidence displays first — but not verified status.
- Prior decision on record: **2026-06-24 rejected an agentic keep/reject verifier** — wrong premise then, and it would make the one deterministic layer non-reproducible. Its future-work clause is the contract this proposal honours: *"Run both deterministic and agentic on the same input, score both against ground truth via the Stage 10 framework. Do not build without this comparison."*
- Constraint check: NLI via HuggingFace stays blocked. The only LLM available on-network is the **existing Power Automate GPT-5.5 proxy** (`src/llmapi.py`) — note it accepts only `{"text": prompt}`: no temperature/seed control, so judge non-determinism cannot be parameterised away, only measured (run the judge twice on the eval set; report agreement).

## Design principles (from George's constraints)

1. **Never silently auto-pass.** Semantic verification only ever *adds a label* (`supported` / `weak` / `unsupported` / `not-assessed`); the existing rapidfuzz `verified` flag is untouched. A cell can be quote-verified but support-unsupported — that combination *displays*, it is never suppressed. Failure of the semantic layer (Ollama down, proxy 5xx) → `not-assessed`, never a pass.
2. **Cost sane at 182 × 4.** Judge at the aggregated-cell level, not per raw evidence item: ≤ 728 cells; realistically ~500 non-empty. With top-3 evidence per cell in one prompt, that's ~500 proxy calls ≈ one-tenth of a production run's extraction calls. Phase A costs nothing (embeddings already computed).
3. **Defensible.** The verifier is itself evaluated against human labels before it labels anything a consultant sees.

## Phase A — semantic-score tiering (free, deterministic, buildable now)

Turn the existing `semantic_score` into a displayed support tier:

- `supported`: quote-verified AND `semantic_score ≥ θ_hi`
- `weak`: quote-verified AND `θ_lo ≤ score < θ_hi`
- `unsupported`: quote-verified AND `score < θ_lo`
- `not-assessed`: no semantic score available

Thresholds **calibrated, not guessed**, on a labelled set (below). Changes needed: an io_excel column + Matrix flag; zero new compute; fully deterministic (same embeddings → same tiers), so the 2026-06-24 reproducibility objection does not apply.

**Known limitation to state honestly:** cosine measures topical similarity, not entailment. It will pass "quote about X, claim about X-but-contradicted" cases. That bounded weakness is exactly what Phase B addresses and what the eval quantifies.

## Phase B — LLM-judge support check (GPT-5.5 proxy), gated on Phase A's eval

One call per aggregated cell: value + its top evidence quotes → strict JSON `{"support": "supports|partial|contradicts|unrelated", "reason": "<one sentence>"}`. Maps onto the same tier column. Triage option to halve cost: only judge cells Phase A couldn't call confidently (`weak` band) — the high/low bands are where cosine is reliable.

Runs as a **post-pipeline pass over the output workbook** (a `diagnostics/`-style script at first), not inside `verify.py` — keeps the deterministic pipeline reproducible and makes the judge re-runnable/skippable per run.

## How we know the verifier is any good (dissertation requirement)

Build a labelled support eval set once; evaluate every candidate verifier against it:

1. **Positives:** GT-aligned verified (value, quote) pairs from the plant-milk cycle (ground_truth_v3 + pipeline output v7 alignment — analyst-endorsed support).
2. **Hard negatives:** permuted pairs — real values matched with real quotes from *other* claims/entities (unrelated), plus same-topic-different-fact pairs (the case cosine should fail).
3. **Human-labelled ADLM sample:** ~100 (value, quote) pairs drawn from the 25-company validation output, labelled supports/partial/no by George (~1–2 h). This is the in-domain test.

Report per verifier (cosine-threshold, LLM-judge, and cosine→judge triage): precision/recall per tier vs human labels, plus judge self-agreement across two runs (the determinism measurement). **Decision rule:** Phase B ships only if the judge beats the calibrated cosine tiers on the human-labelled set by a margin worth ~500 proxy calls per run. If it doesn't, that's a publishable negative result and Phase A stands alone.

This is Stage-10-shaped (aligner/metrics discipline reused on a new target) — it strengthens the evaluation-framework contribution rather than sitting outside it.

## What this is not
- Not a keep/reject filter (2026-06-24 stands: recall is the priority; we annotate, we don't drop).
- Not NLI (blocked) and not a new external API (proxy only).

## Decision points
- **George:** ~1–2 h to label the ~100-pair ADLM support sample; agree the tier vocabulary shown to consultants (`supported/weak/unsupported/not-assessed` vs softer wording).
- **Nick:** sign-off on additional GPT-5.5 proxy volume (~500 calls per 182-run, plus ~300 for the eval set); confirm consultants should see support tiers in client-facing Matrix output (trust/CYA framing is a consultancy call).
