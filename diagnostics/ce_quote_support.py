"""CE quote-support diagnostic: does the ms-marco cross-encoder separate
claims whose cited quote actually answers the question from claims where the
quote is merely topical?

Motivation (2026-07-24): the e2a smoke review found two bad-claim classes —
weak-evidence Yes from menu labels ("Test Development" -> EOL-testing Yes) and
independence-Yes whose citations don't support the verdict. Both are
RELEVANT-but-not-ENTAILING: the quote is about the right topic but doesn't
justify the value. ms-marco scores relevance (query->passage), not
entailment, so the pre-registered prediction is that these suspects score
HIGH on CE(question, quote) — which would be the case for adding an NLI
cross-encoder for a support tier (brain/proposals/semantic-verify.md), not a
refutation of CE for the off-topic tier.

Scores per claim row (Provenance):
  ce_q_quote  CE(question, quote)  — the asymmetric pair; main signal
  ce_v_quote  CE(value, quote)     — quote-supports-value axis (skipped for
                                     bare Yes/No values, meaningless there)
Reports per-question distributions, the bottom of the ranking (candidate
off-topic claims), and where the pre-registered suspect classes land.
No calibration claimed — this measures SEPARATION only.

RESULT (2026-07-24, e2a run, 414 claims): the prediction was WRONG in a more
damning way than expected — ms-marco doesn't score the suspects high, it
scores nearly EVERYTHING zero. 67.6% of claims < 0.01; 14/17 questions have
median <= 0.05, including perfect evidence (Tecan HQ street address scores
0.000 against the headquarters question, while a Tempe quote scores 0.633
only because it contains the word "HEADQUARTERS"). Where it scores high
(employees/PCB/NPI medians 0.74-0.97) the quotes share literal query terms.
Verdict: on marketing prose + fragment quotes, ms-marco relevance degenerates
to lexical overlap — no usable per-claim separation for a verify tier. (The
answerability A/B's AUC 0.772 is not contradicted: AUC is rank-only over
max-of-12-chunk page scores; per-claim absolute separation is what a verify
tier needs and it does not exist.) ce_v_quote is also uninformative (median
0.996 — values are typically verbatim substrings of their quotes). If a
support tier is built, it needs an NLI/entailment cross-encoder, and even
that must be re-tested on these short fragment quotes ("Test Development" is
two words — out of distribution for passage models).

Usage:
    python diagnostics/ce_quote_support.py <run_workbook.xlsx> [--output scores.xlsx]
"""
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import math

import pandas as pd


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


_BARE_BOOL = re.compile(r"^(yes|no|true|false)\.?$", re.IGNORECASE)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workbook")
    ap.add_argument("--output", default=None)
    ns = ap.parse_args()
    wb, out_path = ns.workbook, ns.output

    prov = pd.read_excel(wb, "Provenance")
    rows = prov.dropna(subset=["Verbatim Quote"]).copy()
    rows["Verbatim Quote"] = rows["Verbatim Quote"].astype(str)
    rows["Claim"] = rows["Claim"].astype(str)
    print(f"claims with quotes: {len(rows)}/{len(prov)}")

    from src.eval.cross_encoder import CrossEncoderScorer
    scorer = CrossEncoderScorer()
    scorer.ensure_ready()
    print(f"model: {scorer.name}")

    # --- CE(question, quote): one batched predict for all rows -------------
    jobs = [(q, quote[:1500]) for q, quote in
            zip(rows["Question"], rows["Verbatim Quote"])]
    rows["ce_q_quote"] = scorer.score_pairs(jobs)

    # --- CE(value, quote): only where the value carries content ------------
    substantive = ~rows["Claim"].str.strip().str.match(_BARE_BOOL)
    vq_jobs = [(v, quote[:1500]) for v, quote in
               zip(rows.loc[substantive, "Claim"],
                   rows.loc[substantive, "Verbatim Quote"])]
    rows["ce_v_quote"] = float("nan")
    if vq_jobs:
        rows.loc[substantive, "ce_v_quote"] = scorer.score_pairs(vq_jobs)

    # Percentile of each claim's ce_q_quote within the whole run — "where
    # does this claim sit in the ranking" is the separation readout.
    rows["q_quote_pctile"] = rows["ce_q_quote"].rank(pct=True)

    # --- per-question distribution -----------------------------------------
    print("\n===== CE(question, quote) per question =====")
    per_q = rows.groupby("Question")["ce_q_quote"].agg(["count", "min", "median", "max"])
    per_q = per_q.sort_values("median")
    for q, r in per_q.iterrows():
        print(f"  n={int(r['count']):3d}  min={r['min']:.3f}  med={r['median']:.3f}  "
              f"max={r['max']:.3f}  {q[:70]}")

    # --- verified cross-tab -------------------------------------------------
    if "Verified" in rows.columns:
        print("\n===== ce_q_quote by Verified flag =====")
        vt = rows.copy()
        vt["Verified"] = vt["Verified"].astype(str).str.strip().str.upper()
        print(vt.groupby("Verified")["ce_q_quote"]
                .agg(["count", "mean", "median"]).to_string())

    # --- bottom of the ranking: candidate off-topic claims ------------------
    print("\n===== bottom 15 by CE(question, quote) — candidate off-topic =====")
    cols = ["Claim ID", "Entity", "Question", "Claim", "ce_q_quote", "Verbatim Quote"]
    bottom = rows.nsmallest(15, "ce_q_quote")[cols]
    for _, r in bottom.iterrows():
        print(f"  {r['ce_q_quote']:.3f}  [{r['Claim ID']}] {r['Entity'][:20]:20s} "
              f"Q: {r['Question'][:45]:45s} val: {str(r['Claim'])[:30]:30s} "
              f"quote: {str(r['Verbatim Quote'])[:60]}")

    # --- pre-registered suspect classes -------------------------------------
    # 1) EOL-testing Yes claims (the "Test Development" menu-label case)
    # 2) independence Yes claims (citations don't support, per e2a review)
    suspects = rows[
        (rows["Question"].str.contains("EOL", case=False, na=False)
         | rows["Question"].str.contains("operating independently", case=False, na=False))
        & rows["Claim"].str.strip().str.match(r"^yes\.?$", case=False)
    ]
    print(f"\n===== pre-registered suspects (EOL-Yes + independence-Yes): "
          f"{len(suspects)} claims =====")
    print("prediction: these score HIGH (relevant-not-entailing -> ms-marco blind)")
    for _, r in suspects.sort_values("ce_q_quote").iterrows():
        print(f"  {r['ce_q_quote']:.3f}  (pctile {r['q_quote_pctile']:.0%})  "
              f"[{r['Claim ID']}] {r['Entity'][:20]:20s} "
              f"Q: {r['Question'][:45]:45s} quote: {str(r['Verbatim Quote'])[:60]}")
    if len(suspects):
        med = suspects["q_quote_pctile"].median()
        # Percentiles only mean something if the score distribution has
        # spread. If most of the run sits at ~zero (the 2026-07-24 e2a
        # outcome), rank position is noise — say so instead of over-reading.
        degenerate = (rows["ce_q_quote"] < 0.01).mean() > 0.5
        if degenerate:
            print(f"  -> suspect median percentile: {med:.0%} — NOT interpretable: "
                  f"{(rows['ce_q_quote'] < 0.01).mean():.0%} of ALL claims score "
                  f"<0.01 (degenerate distribution, no separation anywhere)")
        else:
            print(f"  -> suspect median percentile: {med:.0%} "
                  f"({'HIGH — ms-marco does NOT catch these' if med > 0.5 else 'LOW — ms-marco separates them'})")

    if out_path:
        rows.to_excel(out_path, index=False)
        print(f"\nscores written: {out_path}")


if __name__ == "__main__":
    main()
