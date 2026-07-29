"""Join NLI quote-support scores against GT-eval verdicts: does low support
predict the eval's measured errors?

This is the verify-tier ship-gate analysis queued in the 2026-07-24 GT-scoring
brain entry. The eval (generic_eval vs Caitlin's GT) labels each pipeline
answer TP (auto_match / semantic_review) or FP (ai_only); the NLI tier
(nli_quote_support.py --context-premise) scores each Provenance claim's quote
support independently, without ever seeing the GT. If unsupported claims
concentrate in the FP class — in particular the assert-vs-not-disclosed
errors, where the tool asserted Yes/No and the analyst says the site doesn't
disclose — then the NLI tier flags real errors and earns its calibration
labelling; if support is flat across TP/FP, the tier is refuted at this grain.

PRE-REGISTERED EXPECTATIONS (written before the first join, 2026-07-29):
  1. FP (ai_only) median support < TP (auto_match) median support, with a
     usable AUC (>0.70) on the binary capability questions — the
     weak-evidence-Yes class is exactly what run 2 separated on e2a.
  2. The assert-vs-not-disclosed FPs specifically (GT = not disclosed,
     tool asserted) sit in the low-support tail.
  3. Prose grains stay noisy (description/typical-volume: single-quote
     entailment is the wrong grain — known residual class, not a failure).

Join grain: eval Detail row (one pipeline answer) -> best-supported matching
Provenance claim for that (entity, question) whose claim text fuzzy-matches
the answer (rapidfuzz token_sort_ratio >= 85, or exact after lowercasing).
Best-of is deliberate: verify's question is "does ANY quote support this
answer", so the answer's support = its strongest quote. Detail rows without
an AI answer (misses, null agreements) have nothing to support and are
excluded.

RESULTS (2026-07-29, baseline GT-5 join, 115 scored answers):
  EXPECTATION 1 REFUTED at eval grain: AUC 0.535 all / 0.514 non-prose —
  NLI support does NOT predict eval TP-vs-FP. Both classes are support-
  bimodal (TP median 0.979 / FP median 0.987).
  EXPECTATION 2 REFUTED with an interpretable cause: of the 10 scored
  assert-vs-not-disclosed FPs, only 3 sit in the low tail (Adapt low-vol
  Yes 0.000, Avenue NPI Yes 0.001, Asteelflash EOL Yes 0.012 = the weak
  menu-label class). The other 7 score 0.92-1.00 because their quotes DO
  textually support the assertion — e.g. "Avenue Mould focuses exclusively
  on plastic injection moulding" genuinely contradicts PCB capability, so
  the tool's "No" is entailed at 1.000 while Caitlin's GT says Not
  disclosed under the tie->Not-disclosed guidance. The boundary is a
  DISCLOSURE-STANDARD norm (what evidence licenses asserting Yes/No), not
  an entailment gap — no support scorer can arbitrate it.
  EXPECTATION 3 held trivially (prose AUC 0.560, both classes bimodal).
  WHAT SURVIVES: the e2a within-question separation (strong vs menu-label
  evidence) is real but measures evidence QUALITY, not GT agreement. The
  tier's honest role stays annotate-only evidence-strength flagging; it is
  NOT a fix for the assert-vs-not-disclosed miss class. That class is a
  decision-rule alignment: encode the GT guidance's "No needs positive
  evidence; tie -> Not disclosed" norm in the extraction instructions —
  i.e. the already-planned week-2 instructions experiment.

Usage:
    python diagnostics/nli_errors_join.py outputs/nli_support_baseline_gt5.xlsx \
        outputs/cmo_eval_matrix.xlsx [--output outputs/nli_errors_join.xlsx]
"""
import argparse
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import pandas as pd
from rapidfuzz import fuzz

MATCH_RATIO = 85  # token_sort_ratio: eval Detail ai_value <-> Provenance Claim

_TP_VERDICTS = {"auto_match", "semantic_review"}
_FP_VERDICTS = {"ai_only"}

# Prose-grain questions where single-quote entailment is the wrong unit
# (pre-registered residual class c from the 07-24 NLI probe).
_PROSE_MARKERS = ("summary description", "typical production volume")


def _norm(value) -> str:
    return str(value).strip().lower().rstrip(".")


def best_support(nli: pd.DataFrame, entity: str, question: str, ai_value: str):
    """Best-supported matching claim for one eval answer. -> (support, quote)
    or (None, reason) when no claim matches."""
    cand = nli[(nli["Entity"] == entity) & (nli["Question"] == question)]
    if cand.empty:
        return None, "no_claims_for_cell"
    target = _norm(ai_value)
    matched = cand[cand["Claim"].map(
        lambda c: _norm(c) == target
        or fuzz.token_sort_ratio(_norm(c), target) >= MATCH_RATIO)]
    if matched.empty:
        return None, "no_claim_matches_value"
    row = matched.loc[matched["support"].idxmax()]
    return float(row["support"]), str(row["Verbatim Quote"])


def main() -> int:
    ap = argparse.ArgumentParser(description="join NLI support vs eval verdicts")
    ap.add_argument("nli_scores", help="nli_quote_support.py --output workbook")
    ap.add_argument("eval_workbook", help="generic_eval output (Detail sheet)")
    ap.add_argument("--output", default=None)
    ns = ap.parse_args()

    nli = pd.read_excel(ns.nli_scores)
    detail = pd.read_excel(ns.eval_workbook, sheet_name="Detail")

    answers = detail[detail["verdict"].isin(_TP_VERDICTS | _FP_VERDICTS)].copy()
    answers["class"] = answers["verdict"].map(
        lambda v: "TP" if v in _TP_VERDICTS else "FP")
    print(f"eval answers to join: {len(answers)} "
          f"(TP {sum(answers['class'] == 'TP')}, FP {sum(answers['class'] == 'FP')})")

    joined = answers.apply(
        lambda r: best_support(nli, r["entity"], r["question"], r["ai_value"]),
        axis=1)
    answers["support"] = [s for s, _ in joined]
    answers["support_quote"] = [q if s is not None else None for s, q in joined]
    answers["join_gap"] = [None if s is not None else q for s, q in joined]

    unjoined = answers[answers["support"].isna()]
    if len(unjoined):
        print(f"\nWARNING: {len(unjoined)} answers found no matching claim "
              f"(counted, excluded from stats):")
        for reason, n in unjoined["join_gap"].value_counts().items():
            print(f"  {reason}: {n}")

    scored = answers.dropna(subset=["support"]).copy()
    scored["prose_grain"] = scored["question"].str.lower().map(
        lambda q: any(m in q for m in _PROSE_MARKERS))

    def _block(name: str, frame: pd.DataFrame) -> None:
        print(f"\n===== {name} =====")
        for cls in ("TP", "FP"):
            sub = frame[frame["class"] == cls]["support"]
            if sub.empty:
                print(f"  {cls}: n=0")
                continue
            print(f"  {cls}: n={len(sub):3d}  median={sub.median():.3f}  "
                  f"q25={sub.quantile(.25):.3f}  q75={sub.quantile(.75):.3f}")
        tp = frame[frame["class"] == "TP"]["support"]
        fp = frame[frame["class"] == "FP"]["support"]
        if len(tp) and len(fp):
            # Mann-Whitney AUC: P(random TP support > random FP support).
            wins = sum((t > f) + 0.5 * (t == f) for t in tp for f in fp)
            print(f"  AUC (support separates TP from FP): "
                  f"{wins / (len(tp) * len(fp)):.3f}")

    _block("ALL joined answers", scored)
    _block("non-prose grains (the tier's intended scope)",
           scored[~scored["prose_grain"]])
    _block("prose grains (pre-registered wrong-grain residual)",
           scored[scored["prose_grain"]])

    # Assert-vs-not-disclosed FPs live in ai_only rows whose OWN gt_value is
    # blank — the GT-null marker sits on the sibling auto_miss/null_match row
    # of the same cell. Identify the cells first, then take their FPs.
    null_cells = set(map(tuple, detail[
        detail["gt_value"].astype(str).str.contains(
            "not disclosed", case=False, na=False)][["entity", "question"]].values))
    nd = scored[(scored["class"] == "FP")
                & scored.apply(lambda r: (r["entity"], r["question"]) in null_cells,
                               axis=1)]
    print(f"\n===== assert-vs-not-disclosed FPs (expectation 2: low tail) =====")
    for _, r in nd.sort_values("support").iterrows():
        print(f"  {r['support']:.3f}  {r['entity'][:22]:22s} "
              f"Q: {str(r['question'])[:44]:44s} answered: {str(r['ai_value'])[:20]}")

    print("\n===== lowest-support TPs (over-flag risk if tier ever gates) =====")
    for _, r in scored[scored["class"] == "TP"].nsmallest(5, "support").iterrows():
        print(f"  {r['support']:.3f}  {r['entity'][:22]:22s} "
              f"Q: {str(r['question'])[:44]:44s} answered: {str(r['ai_value'])[:20]}")

    if ns.output:
        answers.to_excel(ns.output, index=False)
        print(f"\njoined rows written: {ns.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
