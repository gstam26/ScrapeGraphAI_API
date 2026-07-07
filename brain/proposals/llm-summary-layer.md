# Proposal: LLM summary layer — synthesized prose over verified claims

**Status: BUILT (2026-07-07) — `SUMMARY_ENABLED=False` until the §4 ship bar passes.** George's green light received 2026-07-07 (his §5–§7 re-review + Nick's Azure-direct sanction); the §7 checklist cleared the same day via `diagnostics/azure_test.py` (seed probe: identical outputs, stable `fp_b7c8a4dc64` — §5's assumption confirmed on the deployment). Implementation: `src/summarize.py` (summarizer + Tier-1 gate), `pipeline.py` hook, `config.py` SUMMARY_* block, io_excel "AI Summary" + "Summary Log" sheets, `diagnostics/summary_judge.py` (Tier-2), `diagnostics/summary_eval.py` (judge-validation harness: positives / corruptions / self-agreement / label-template / label-score). 28 offline tests; suite 161. Remaining: run the eval on the work laptop, George's ~50-summary labelling, then flip the flag only if the pre-registered bar passes. See decision-log 2026-07-07.

*Original status (2026-07-06 evening):* DESIGN ONLY, revised. George approved the four design decisions (grouped-scaffolding input; separate AI Summary sheet; the pre-registered ship bar, held even if output reads well; George does the ~50-summary labelling) and directed one implementation change: **the LLM path is Azure GPT-4.1-mini via the existing Azure implementation, not the Power Automate GPT-5.5 proxy.** This revision reflects that switch (§5, §6, work-laptop checklist). The precondition — unverified claims excluded from grouping/digest — **was built and tested 2026-07-06** (see "Precondition shipped" below).

**Environment caveat honoured throughout:** everything below about the Azure path is read from committed code (`src/extract.py`, `config.py`). The live endpoint, keys, deployment, and quota exist only on George's work laptop and are NOT verifiable from this machine — every such item is listed in the work-laptop checklist (§7) rather than asserted.

## What Nick asked for and what it breaks

Nick wants readable synthesized prose per cell — the thing the deterministic Digest deliberately isn't (decision-log 2026-07-03: "LLM prose would need a faithfulness eval to earn the same trust"). That trade was recorded as a future option; this design is that option, priced honestly. LLM prose breaks two properties every other layer guarantees:

1. **Verifiability against a source span.** Every claim in the pipeline has a verbatim quote checkable against cached page text (`src/verify.py:_verify_quote`). Synthesized prose has no source span — it can only be verified against its *inputs*.
2. **Determinism.** Every layer from aggregate through Digest is a pure function of its inputs (grouping is deterministic given embeddings, `src/group.py` module docstring). An LLM step is not — though the Azure path lets us *reduce* the non-determinism (temperature=0 + seed, §5) rather than only measure it, which the old proxy path (`{"text": prompt}` only, `src/llmapi.py:33`) could not.

The design therefore walls the summarizer off: it adds one clearly-labelled artifact, gates it behind a faithfulness check, and leaves every existing sheet byte-identical whether it runs or not.

## 1. Where it sits

**Inside `run_pipeline`, immediately after grouping, fail-soft — not the `summarize_cells`-after-aggregate sketch from the layer-explanation session, and not a separate post-run script.**

Insertion point: `pipeline.py:395-401`, directly after the `GROUPING_ENABLED` block. The summarizer consumes `diag["claim_groups"]` (the grouping output — see §2), so it must run after `group_rows` anyway; the earlier sketch of a `cell.summary` field populated between `aggregate_cells` and the Matrix render is **wrong for this design** because at that point the theme structure doesn't exist yet and the field would sit on the deterministic `ExtractedCell` model, blurring the wall.

```python
# pipeline.py, after the GROUPING_ENABLED block
if SUMMARY_ENABLED and diag.get("claim_groups"):
    try:
        diag["cell_summaries"] = summarize_groups(diag["claim_groups"])
    except Exception as exc:
        _safe_print(f"! Summarization skipped: {exc}")
```

Mirrors the grouping pattern exactly (`pipeline.py:395`): config-gated (`SUMMARY_ENABLED`, default False until the eval passes), any failure — Azure unreachable, retries exhausted, missing `AZURE_API_KEY` in `.env` — only skips the sheet, never fails the run, and never touches `result.rows`. Rejected alternatives:

- **In-pipeline after aggregate (`cell.summary`)**: puts a non-deterministic field on the core model; summaries couldn't cite theme structure; Matrix render code would need to know about it. Rejected.
- **Post-run script over the workbook** (the semantic-verify Phase B pattern): cleanest wall, but two commands for Advisory to run and a second window for things to go stale. The wall is preserved just as well by fail-soft + separate sheet, and deployability beats elegance for this user base (one command, one workbook). The *judge* stays post-run (§4) — it's diagnostics, not deliverable. Rejected for the summarizer itself.

New module `src/summarize.py`; new config block `SUMMARY_ENABLED = False`, `SUMMARY_MAX_CLAIMS_PER_THEME` (prompt cap, see §5), `SUMMARY_TIMEOUT`.

## 2. Input — the one real design decision

**Decision: the summarizer consumes the grouped-theme structure (`diag["claim_groups"]` + claim IDs from `claim_index`), not raw verified claims. George's lean is right; here is the case from the code:**

1. **Verified-only is already enforced there, at a single choke point.** As of this session, `src/group.py:_display_values` drops any value without at least one verified evidence item, before `GROUP_MIN_ITEMS` is applied. Feeding the summarizer from grouping output means the "never sees unverified claims" guarantee is inherited from one tested function rather than re-implemented (and re-testable) in a second place. Raw-claims input would need its own filter with its own tests and its own drift risk.
2. **The faithfulness eval becomes mechanical where it matters most.** Each theme member already carries a claim ID resolvable via `claim_index` (`src/io_excel.py:_make_provenance_df`). Give the LLM a closed set of `[C####]`-tagged claims and require citations; then "did it invent a source?" is a set-membership check, not a judgment call. Raw claims could be tagged too, but grouping also bounds *coverage* checking: "no dropped critical facts" is operationalizable as "every top-K theme is represented" (§4), which has no analogue in an unstructured claim list.
3. **Token control on pathological cells.** HORIBA's Recent news cell is 328 verified claims; raw input is a ~20k-token prompt with no principled truncation. Grouped input compresses to 15-19 themes × (label + n_items + a capped sample of members) — the summary is about the shape of the cell, and the shape is exactly what grouping computed. Truncation becomes principled: drop members within a theme, never whole themes.
4. **The traceability chain stays one chain.** Summary prose cites claim IDs → Provenance; its themes correspond to Grouped Themes rows a consultant can already click through. Raw-claims input would produce prose organized differently from the sheets beneath it — two competing structures of the same cell.

**Cost of this choice, stated honestly:** the summarizer inherits grouping's errors. A bad cluster (or a medoid that mislabels its members) becomes a misleading summary sentence, and GROUP_SIMILARITY=0.15 was calibrated for scannability (6-19 themes), not semantic purity. Mitigations: the faithfulness judge checks sentences against the *member claims* the LLM actually saw (not the theme label), so a mislabeled cluster still gets caught if the prose follows the label rather than the members; and the human sanity-read gate on theme coherence (decision-log 2026-07-03) remains a prerequisite for client-facing use.

Prompt shape (per grouped cell, one call):

```
You are summarizing verified extracted claims about {entity} for the question "{question}".
Claims are grouped into themes. Every statement you write MUST cite the claim IDs
it draws from, in square brackets. Do not state anything not supported by a cited claim.
Write 2-4 sentences.

Theme "Anticoagulant Monitoring" (19 claims): [C0915] Anticoagulant Monitoring; [C0917] ...
Theme "Blood Gas Analysis" (12 claims): [C0532] ...; ...
... (+3 smaller themes: "X" (4), "Y" (3), "Z" (2))
```

Cells below `GROUP_MIN_ITEMS` arrive as one "(all items)" group — same prompt, no theme labels. Cells whose values are all unverified never reach the summarizer at all (they produce no group).

## 3. Output — walled off and cited

- **Own artifact:** `diag["cell_summaries"]` — list of dicts `{entity, question, summary, cited_ids, uncited_sentences, model, prompt_version, generated_at}`. Never a field on `ExtractedCell`; `result.rows` untouched.
- **Own sheet:** new **"AI Summary"** sheet, one row per summarized cell: Entity | Question | Summary | Claim IDs Cited | Faithfulness | Model. Inserted after Digest in the sheet order. Row 1 note or header suffix: *"AI-synthesized prose (Azure GPT-4.1-mini). Not verified text — every statement cites Claim IDs; check them in Provenance."* A separate sheet rather than a column on Digest keeps the deterministic/synthesized boundary physically visible: everything on Digest is faithful by construction, everything on AI Summary is not, and a consultant can tell which regime they're reading from the tab they're on. **(Approved by George 2026-07-06.)**
- **Cited:** every sentence must carry ≥1 `[C####]`; the mechanical gate (§4) enforces it at write time. The Claim IDs Cited column makes the citation set filterable without parsing prose.
- **Never replaces anything:** Matrix, Digest, Grouped Themes, Provenance are written identically with `SUMMARY_ENABLED` on or off. If a summary fails the mechanical gate, its row shows the deterministic Digest line instead, with Faithfulness = "fallback (failed citation gate)" — degradation is visible, never silent.
- **Non-determinism reduced AND handled:** calls set `temperature=0` and a fixed `seed` (§5), and record the returned `system_fingerprint`, so re-runs should be near-identical rather than merely audited. The exact prompt and raw response are still stored in `diag` (Extract-Log-style "Summary Log" diagnostics sheet, DIAGNOSTICS-gated) because seed-based determinism is best-effort per OpenAI's own documentation (backend changes, signalled by a changed `system_fingerprint`, can alter output) — any given workbook stays auditable regardless. The sheet carries `prompt_version` so prose is never compared across prompt changes.

## 4. Faithfulness evaluation — what makes this defensible

Prose is verified against its inputs: every statement must trace to a claim the model was given, and no critical input may be dropped. Two tiers, mirroring semantic-verify's two-phase discipline:

**Tier 1 — mechanical gate (deterministic, free, runs inline at write time):**
- Every cited `[C####]` ∈ the input set (no invented citations) — set membership.
- Every sentence carries ≥1 citation (uncited prose = unfalsifiable prose) — regex + sentence split.
- Coverage: each of the top-3 themes by n_items (the same top-3 Digest cites) is represented by ≥1 citation from its member set. "Critical" = what the deterministic layer already deems headline-worthy.
- Fail any check → fall back to the Digest line (§3). This gate alone eliminates the worst failure classes (fabricated sources, orphan assertions) with zero LLM cost.

**Tier 2 — LLM-judge (Azure GPT-4.1-mini, post-run diagnostics pass, semantic-verify Phase B pattern):** per summary, one call: each sentence + the full text of the claims it cites → strict JSON `{"sentence_n": "faithful|unsupported|contradicted"}`. Catches what mechanics can't: a sentence citing real IDs while asserting something they don't say. Written to the Faithfulness column (`faithful` / `n flagged sentences` / `not-assessed`). Judge failure → `not-assessed`, never a pass (semantic-verify principle 1). Whether mini is *capable enough* for this role is now an explicit open question — see §6.

**Validating the judge itself (the labelled-pairs pattern, before trusting it):**
1. **Positives, free and by construction:** the deterministic Digest lines are template-assembled from theme structure — faithful summaries by construction. ~89 per validation run, zero labelling cost.
2. **Hard negatives, by construction:** programmatic corruptions of real generated summaries — swap an entity/number, inject a fact absent from the input claims, delete the largest theme's sentence, re-attach a citation to the wrong sentence. Label known by construction; this is semantic-verify's permuted-pairs move applied to prose.
3. **Human in-domain sample:** George labels ~50 real generated summaries from the 25-company validation output (sentence-level faithful/not, ~1-2 h — same budget shape as the semantic-verify labelling ask).
4. **Judge self-agreement:** run the judge twice on the same set; report agreement. With `temperature=0` + fixed seed (§5) this should now be ≈1.0 — the leg changes character from *measuring an unavoidable cost* (the proxy offered no determinism controls) to a *cheap confirmation that the controls actually hold on this deployment* (seed support is a work-laptop check, §7, and best-effort even when supported). The bar itself does not move.

**Pre-registered ship bar (so we can't rationalise after — approved by George 2026-07-06, to be held even if the output reads well):** judge ≥0.90 accuracy on the corruption set, ≥0.80 sentence-level agreement with George's labels, self-agreement ≥0.90. `SUMMARY_ENABLED` flips to True in a client-facing config only after the bar passes; below the bar, the negative result is written up and the deterministic Digest stands alone (that outcome is dissertation-usable either way). What a failure would mean and what happens next: §6.

## 5. The Azure path, determinism, and cost (revised 2026-07-06 per George)

### What the committed code exposes

The existing Azure implementation is `_extract_with_azure` (`src/extract.py:297-356`), dispatched as `EXTRACT_TOOL="azure"` (`src/extract.py:681-682`; also the config default, `config.py:28`). Read from the file, not assumed:

- **Client:** plain `OpenAI(base_url=AZURE_ENDPOINT, api_key=AZURE_API_KEY)` from the `openai` SDK (`extract.py:323`; `openai` is in `requirements.txt:13`) — not the `AzureOpenAI` class. This works because the committed default endpoint is Azure's OpenAI-v1 compatibility surface: `https://thebeastgpu.openai.azure.com/openai/v1` (`config.py:13`). API-versioning is handled server-side on that path.
- **Call:** `client.chat.completions.create(model=AZURE_DEPLOYMENT, messages=[...], timeout=EXTRACT_TIMEOUT)` (`extract.py:325-329`). `AZURE_DEPLOYMENT` defaults to `"gpt-4.1-mini"`, env-overridable (`config.py:14`).
- **Temperature/seed:** **not set anywhere in the committed call** — only `model`, `messages`, `timeout` are passed. The SDK's `chat.completions.create` accepts `temperature` and `seed` parameters, so the *code path supports adding them* trivially; whether this *deployment* honours `seed` (and returns `system_fingerprint`) cannot be confirmed from this repo — work-laptop check, §7.
- **Retry:** the hand-rolled 5xx retry lives **only** in `src/llmapi.py:call()` (`llmapi.py:35-47`) and does **not** wrap the Azure path — `_extract_with_azure` never touches `LLMAPI`, catches all exceptions, and fails soft to `{}` with `retry_count` hardcoded 0 (`extract.py:319`). However, the OpenAI SDK itself auto-retries connection errors, 408/409/429 and 5xx with backoff (SDK default `max_retries=2`; the committed code doesn't override it), so Azure calls already get SDK-level retries the proxy path had to hand-roll.

### How the summarizer and judge use it

Both route through a thin shared helper in the new `src/summarize.py` (NOT through `llmapi.py`, NOT through `_extract_with_azure` — that function is extraction-prompt-specific), reusing `AZURE_*` config and the `_extract_with_azure` error-handling pattern:

```python
client.chat.completions.create(
    model=AZURE_DEPLOYMENT,
    messages=[{"role": "user", "content": prompt}],
    temperature=0,
    seed=SUMMARY_SEED,          # fixed, in config
    timeout=SUMMARY_TIMEOUT,
)
```

plus: record `completion.system_fingerprint` per call; a small semaphore for concurrency (reuse the `EXTRACT_MAX_CONCURRENT_CALLS` pattern, `config.py:246`, sized to the deployment's rate limits — §7); timeout/exception handling copied from `_extract_with_azure` (fail-soft, never fails the run).

### Determinism, revised

With `temperature=0` + fixed `seed`, non-determinism is *reduced at source* rather than only measured — a materially better position than the proxy's opaque `{"text": prompt}` interface. Stated honestly: OpenAI documents seeded determinism as best-effort (backend updates, signalled by a changed `system_fingerprint`, can still alter output), so §3's audit trail (prompt + raw response + fingerprint stored per call) stays. Effect on the eval: the self-agreement leg becomes a confirmation that the controls hold on this deployment (expected ≈1.0) instead of a measurement of unavoidable variance; the ≥0.90 bar is unchanged. If seed turns out unsupported on this deployment (§7 check), the design still works — we're back to measuring, exactly as originally specified.

### Cost, recomputed at real GPT-4.1-mini pricing

List pricing (OpenAI/Azure pay-as-you-go, as of my Aug-2025 knowledge — confirm current Azure regional pricing on the portal, §7): **$0.40 per 1M input tokens, $1.60 per 1M output tokens.**

Volume for a 178-company run (scaled from the 2026-07-03 validation workbook: 89 grouped cells / 25 entities; 322 themes / 89 cells ≈ 3.6 themes/cell; members capped at `SUMMARY_MAX_CLAIMS_PER_THEME` ≈ 15):

| Component | Calls | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| Summaries (89/25 × 178) | ~634 | ~0.95M (~1,500/call) | ~0.13M (~200/call) | ~$0.58 |
| Judge (one per summary) | ~634 | ~1.14M (~1,800/call: claims + prose) | ~0.10M | ~$0.61 |
| **Per full 178-run** | **~1,270** | **~2.1M** | **~0.23M** | **≈ $1.20** |
| One-off eval validation (89 digest-positives + ~150 corruptions + 2nd self-agreement pass) | ~480 | ~0.9M | ~0.07M | ≈ $0.50 |

So: **roughly $1–2 per full run, under $5 including the one-off eval** — this replaces the old "proxy spend not visible" assumption with an actual figure. Cost is no longer a decision factor at all; the binding operational constraint shifts to the deployment's TPM/RPM quota (unknown from here — §7), which sizes the semaphore and the wall-clock, not the budget.

## 6. Model capability: GPT-4.1-mini vs GPT-5.5 — an open question the eval answers

GPT-4.1-mini is a substantially weaker model than the GPT-5.5 the design originally assumed. The reason it is *plausibly* sufficient is that both roles are deliberately constrained and scaffolded: the summarizer compresses a **closed, claim-ID-tagged, pre-grouped** set (no retrieval, no world knowledge required — world knowledge is precisely the hazard the citation gate exists to catch), and the judge does sentence-level entailment against **provided** claims, not open-ended fact-checking. These are the task shapes small models handle best. But "plausibly sufficient" is now a hypothesis the eval tests, not an assumption the design makes. **The ship bar stays exactly as pre-registered** (George, 2026-07-06: held even if the output reads well).

### If mini fails the bar: diagnosis path

Which leg fails localises the cause — run in this order:

1. **Self-agreement < 0.90** (despite temperature=0 + seed): not a capability signal. Check `system_fingerprint` variance across the two runs (backend drift) and JSON-parse stability (output-format flakiness). Fix output format / confirm seed support before concluding anything about the model.
2. **Corruption set < 0.90**: the judge can't do even scaffolded entailment reliably. Break accuracy down by corruption type (injected fact vs swapped number/entity vs dropped theme vs re-attached citation). Uneven failures → **prompt scaffolding** is the lever: per-sentence binary verdicts instead of one batched JSON, few-shot examples of each corruption type, reason-then-verdict ordering. Flat failures across all types and scaffolding variants → **genuine capability gap**.
3. **Corruption set passes but George-label agreement < 0.80**: the synthetic notion of faithfulness diverges from the human one. Review the disagreement cases together — either the rubric needs tightening (task-definition problem, not model) or mini misses subtle unsupported *implications* that programmatic corruptions don't capture (capability, the subtle kind).

**Scaffolding iteration is bounded to two rounds**, each re-scored against the same fixed eval sets — pre-registered here so prompt-tuning can't degenerate into quiet bar-shopping. **George's labels are never wasted by a failure:** they label fixed (summary, claims) pairs and validate the *judge*; changing the judge's prompt or even its model reuses them as-is. Only regenerating the summaries themselves would need a fresh sample.

### Fallback ladder (in order — George, 2026-07-06)

1. **Improve prompt/scaffolding** within the bounded iterations above.
2. **Deterministic Digest stands as the shipped feature.** This is the zero-regret floor: it exists, it's faithful by construction, it shipped 2026-07-03. `SUMMARY_ENABLED` stays False; the negative result is written up (dissertation-usable).
3. **Escalate model choice to George + Nick.** Options at that point, cheapest first: a stronger model for the judge only (the judge is the trust-critical role, ~half the calls); a larger Azure deployment if one exists on the resource; back to the GPT-5.5 proxy for both roles — reopening the determinism and throughput trade this revision closed.

## 7. Work-laptop checklist — what George must verify (not confirmable from this machine)

This Dell has no `.env` at all (confirmed 2026-07-05), so every item below is laptop-only:

1. **Keys:** `AZURE_API_KEY` present in the work-laptop `.env` (`config.py:12` reads it; `extract.py:305-306` raises without it).
2. **Deployment:** a deployment matching `AZURE_DEPLOYMENT` (default `"gpt-4.1-mini"`, env-overridable, `config.py:14`) exists on the `thebeastgpu` resource and actually serves GPT-4.1-mini.
3. **Sanction:** Azure-direct is the approved LLM path now. The previously recorded compliance position was "the only LLM available on-network is the Power Automate proxy" (`semantic-verify.md`, 2026-07-02) — George's instruction reverses that; worth having the reversal confirmed in writing (Nick/IT) since it's a policy change, not just a config change.
4. **Quota:** the deployment's TPM/RPM limits — sizes `SUMMARY_MAX_CONCURRENT_CALLS` and the expected wall-clock for ~1,270 calls; also whether extraction (same deployment, `EXTRACT_TOOL=azure` runs) and summarization would contend if run together.
5. **Seed support:** one probe prompt sent twice with `temperature=0, seed=42` — identical output and a non-null `system_fingerprint` confirm §5's reduced-nondeterminism assumption; if not honoured, the self-agreement leg reverts to measuring (design unchanged otherwise).
6. **Network path:** the validation run showed corporate TLS interception breaking `api.firecrawl.dev` calls on-network (self-signed cert in chain, EUROIMMUN ×3); confirm `thebeastgpu.openai.azure.com` is not similarly intercepted from the Sagentia network (the probe call in item 5 doubles as this check). It was TCP-reachable from this Dell on a residential connection (2026-07-05), which says nothing about on-network behaviour.
7. **Pricing:** confirm current GPT-4.1-mini rates on the Azure portal for the actual region/contract — §5's $0.40/$1.60 per 1M tokens is list pricing as of my knowledge cutoff.

## Precondition shipped this session (built, not design)

George's standing decision — unverified claims excluded from grouping/digest — is implemented and tested:
- `src/group.py:_display_values` + new `_verified_norms`: a value enters grouping only with ≥1 verified evidence item ("any-verified" semantics: a claim confirmed on one of two pages is verified). Applied before `GROUP_MIN_ITEMS`. Values with no evidence at all (e.g. synthesized union-list strings — the audit's gap #7 silent no-anchor path) are unverifiable → excluded.
- `src/io_excel.py:_make_provenance_df`: `claim_index` now anchors each claim on its first VERIFIED Provenance occurrence (fallback: first occurrence) — closes the audit's gap-#1 leak where a verified-somewhere claim could still hyperlink to an unverified duplicate row.
- `src/io_excel.py:_style_sheet`: the orange Verified=False review flag actually renders now (the old check compared `"FALSE"` against Python bool `str()` output `"False"` — dead code). Unverified claims "stay in Provenance flagged for analyst review" is now literally true on the sheet.
- Tests: 5 new (3 grouping-policy, 2 traceability), fixtures updated to the new invariant; suite 125 passed.
- Audit validation criterion now enforced by construction: every Grouped Themes anchor and Claim ID resolves to a Verified=True row whenever one exists, and only verified claims are cited at all.

## Decision points

- **George — DECIDED 2026-07-06:** (a) grouped-scaffolding input ✓, (b) separate "AI Summary" sheet ✓, (c) pre-registered ship bar, held even if output reads well ✓, (d) George does the ~50-summary labelling ✓. Azure GPT-4.1-mini directed as the LLM path.
- **George (blocking for build):** re-review this Azure revision; complete the work-laptop checklist (§7); confirm the verified-only base validates clean on the 25-sample re-run. Build starts only on his green light.
- **Nick (blocking for production):** confirm Azure-direct is the sanctioned path (§7 item 3 — a reversal of the recorded proxy-only position); confirm consultants may see AI-synthesized prose in a client-facing workbook given the marking/citation/fallback regime above. (The old proxy-throughput concern is moot; cost is ≈$1-2/run at mini pricing.)
