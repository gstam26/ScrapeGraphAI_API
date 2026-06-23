# Decision Log

Append-only record of design decisions and deliberately-deferred ideas.
Newest first. Each entry: date, decision, rationale, status.

---

## 2026-06-23 — Agentic LLM-as-judge verification: deferred (Could-tier future work)

**Decision:** Do NOT add an agentic / LLM-as-judge keep-reject filter to the verify
layer now. File as a candidate Could-tier experiment.

**Rationale (from Stage 10 metrics on the real run):**
- Redundancy is not the main precision issue: precision_strict 0.73 vs
  precision_distinct 0.74 — the tiny gap means *gaps dominate, not duplicates*.
- Hallucination is already 0 (after the spurious-null reclassification).
- The 58 "gaps" are mostly source-verified real claims and granularity splits,
  not weak or duplicate claims.
- An agentic keep/reject filter would lower **recall** (the priority metric) to fix
  a smaller problem, and would inject non-determinism into a verify layer we have
  deliberately kept deterministic and reproducible (rapidfuzz baseline).

**If ever implemented — required evaluation:** Must be evaluated AGAINST the
deterministic rapidfuzz baseline using the Stage 10 framework: run both verifiers,
score both against ground truth, report the precision/recall delta. Shipping it
without that measurement would be an unmeasured change to a working, reproducible
layer.

**Status:** Not implemented. Deferred.
