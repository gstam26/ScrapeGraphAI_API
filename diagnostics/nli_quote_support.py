"""NLI quote-support diagnostic: can an entailment cross-encoder provide the
verify layer's missing support tier (quote SUPPORTS the answer, not merely
quote EXISTS — layers/verify.md "entailment gap")?

Follow-up to ce_quote_support.py, which refuted ms-marco for this role
(2026-07-24: relevance scores collapse to lexical overlap on marketing
prose). NLI is the architecturally right formulation: premise = the verbatim
quote, hypothesis = a declarative statement of (entity, question, value),
support = P(entailment). For binary No-claims the hypothesis stays
affirmative and support = P(contradiction) — one hypothesis per question,
the class probabilities carry the polarity.

Hypothesis templates cover the 17 CMO v2 questions (matched by substring);
unmatched questions fall back to a generic "{entity}: {value}" hypothesis
and are marked template=generic in the dump. Production generalization would
need a hypothesis template authored per question (natural home: a column on
the questions sheet, next to instructions) — diagnostic-scope for now.

PRE-REGISTERED EXPECTATIONS (written before the first run, 2026-07-24):
  1. Distribution must NOT collapse (the ms-marco failure). Fragment quotes
     ("Test Development" = two words) are outside NLI training distributions
     too — if everything lands neutral, NLI is refuted the same way.
  2. Tecan HQ street-address quote ("Seestrasse 103, 8708 Männedorf,
     Switzerland") vs "Tecan's headquarters is located in Männedorf,
     Switzerland" -> HIGH entailment (the exemplar ms-marco scored 0.000).
  3. Menu-label "Test Development" vs "...has end-of-line testing
     capability" -> LOW entailment (the weak-evidence Yes the e2a review
     flagged; catching it is the tier's whole point).
  4. Independence-Yes generic quotes ("global product realization services
     company", privacy-policy boilerplate) -> LOW/neutral. Caveat: "shares
     traded on SIX Swiss Exchange" is genuinely moderate evidence of
     independence — a human would debate it; flagging it for review is
     acceptable behaviour, not an error.
Success bar: (a) spread in P(entail); (b) exemplar 2 scores high while
exemplar 3 scores low — known-good/known-bad separation in the right
direction; (c) per-question medians vary sensibly. No threshold is
calibrated here; verdict bands below are for FLAGGING only (semantic-verify
contract: tiers annotate, never silently gate).

RESULTS (2026-07-24, e2a, 414 claims, nli-deberta-v3-base):
  RUN 1 (bare quote as premise): expectation 1 half-failed — real spread
  exists (top-8 all correct, incl. P(contradiction) support for binary-No:
  "Manufacturing Facilities in the US and Mexico" vs exclusively-China ->
  1.000, genuine inference) but 274/414 (66%) flagged unsupported including
  obviously good evidence. Failure pattern: fragment quotes with no subject
  (Tecan address 0.000 — exemplar 2 failed) and we/the-company coreference
  ("the company has 3,000 employees" 0.004). The model is a correct but
  context-blind entailment judge.
  RUN 2 (--context-premise, "From {entity}'s website: <quote>"): median
  0.009 -> 0.962, unsupported 66% -> 43%, and WITHIN-QUESTION SEPARATION
  appears on the capability binaries — EOL now splits "Full product test
  and validation" 0.988 / "Full system assembly, test, and validation"
  0.981 (supported) from "Test Development" 0.001 / "Environmental Stress
  Screening" 0.000 (the weak menu-label evidence the e2a review flagged —
  exemplar 3 held in both runs). NPI 0.001->0.962, systems integration
  0.009->0.949, tooling ->0.966, employees ->0.996 medians. Independence
  stays mostly low EXCEPT the two genuinely informative quotes ("Mack —
  founded in 1920 and debt-free" 0.761; "veteran owned, debt free" 0.691)
  — right ordering for an absence-type question.
  RESIDUAL FLAG CLASSES (interpretable, not noise): (a) HQ address
  fragments still fail (0.008 — an address block strictly doesn't entail
  "headquarters"; candidate fix: add page title to the context premise);
  (b) acquirer template wording ("wholly owned subsidiary of X" does not
  entail "was ACQUIRED by X" — template fix, not model); (c) description
  cells (value synthesizes multiple pages, single-quote entailment is the
  wrong grain); (d) strict-numeric hypotheses (low-volumes "around
  500-1000" can't be entailed by a quote that gives no numbers — arguably
  the flag is CORRECT). --context-premise is the recommended mode.
  STATUS: promising, uncalibrated. Ship gate = human-labelled support pairs
  (the semantic-verify eval set); annotate-only per the 2026-06-24 contract.

Network note: downloads the model from HuggingFace on first run — works on
this Dell, blocked on the Sagentia network. On the work laptop, download
elsewhere and pass --model <local_path> (the Paulo_cross_encoder pattern).

Usage:
    python diagnostics/nli_quote_support.py <run_workbook.xlsx>
        [--model cross-encoder/nli-deberta-v3-base] [--output scores.xlsx]
"""
import argparse
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import pandas as pd

_YES = re.compile(r"^(yes|true)\.?$", re.IGNORECASE)
_NO = re.compile(r"^(no|false)\.?$", re.IGNORECASE)

# question-substring -> hypothesis template. {e} = entity, {v} = claim value.
# Binary questions get ONE affirmative hypothesis; polarity is read off the
# entail/contradiction probabilities, not encoded in the text.
_TEMPLATES = [
    ("summary description", "{v}"),
    ("operating independently", "{e} is an independent company."),
    ("printed circuit board", "{e} has printed circuit board manufacturing or assembly capability."),
    ("systems integration", "{e} has systems integration capability."),
    ("end-of-line", "{e} has end-of-line product testing capability."),
    ("new product introduction", "{e} provides new product introduction support."),
    ("medical device manufacturing", "{e} has experience of medical device manufacturing."),
    ("headquarters", "{e}'s headquarters is located in {v}."),
    ("country/countries does manufacturing", "{e} has manufacturing operations in {v}."),
    ("tooling capability", "{e} has tooling capability."),
    ("exclusively in china", "{e}'s manufacturing takes place exclusively in China."),
    ("how many employees", "{e} has {v} employees."),
    ("yearly revenue", "{e}'s yearly revenue is {v}."),
    ("plastic moulding", "{e} has plastic moulding capability."),
    ("typical production volume", "{e}'s typical production volume is {v}."),
    ("acquired or absorbed", "{e} was acquired or absorbed by {v}."),
    ("low volumes", "{e} produces low volumes of around 500 to 1000 products per year."),
]


def build_hypothesis(question: str, entity: str, value: str):
    """-> (hypothesis, polarity, template_kind). polarity: which class
    probability counts as support — 'entail' or 'contradiction'."""
    q = question.lower()
    polarity = "contradiction" if _NO.match(value.strip()) else "entail"
    for needle, tpl in _TEMPLATES:
        if needle in q:
            return tpl.format(e=entity, v=value), polarity, "cmo_v2"
    return f"{entity}: {value}", polarity, "generic"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workbook")
    ap.add_argument("--model", default="cross-encoder/nli-deberta-v3-base")
    ap.add_argument("--output", default=None)
    ap.add_argument("--context-premise", action="store_true",
                    help="prepend 'From {entity}'s website: ' to each quote — "
                         "tests whether run-1 failures are missing premise "
                         "context (fragments, we/the-company coreference) "
                         "rather than entailment itself")
    ns = ap.parse_args()

    prov = pd.read_excel(ns.workbook, "Provenance")
    rows = prov.dropna(subset=["Verbatim Quote"]).copy()
    rows["Verbatim Quote"] = rows["Verbatim Quote"].astype(str)
    rows["Claim"] = rows["Claim"].astype(str)
    print(f"claims with quotes: {len(rows)}/{len(prov)}")

    hyps = [build_hypothesis(q, e, v) for q, e, v in
            zip(rows["Question"], rows["Entity"], rows["Claim"])]
    rows["hypothesis"] = [h for h, _, _ in hyps]
    rows["polarity"] = [p for _, p, _ in hyps]
    rows["template"] = [t for _, _, t in hyps]
    n_generic = (rows["template"] == "generic").sum()
    if n_generic:
        print(f"WARNING: {n_generic} claims fell back to the generic template")

    from sentence_transformers import CrossEncoder
    model = CrossEncoder(ns.model)
    id2label = {i: l.lower() for i, l in
                model.model.config.id2label.items()}
    print(f"model: {ns.model}  labels: {id2label}")

    import numpy as np
    if ns.context_premise:
        print("premise mode: CONTEXT (From {entity}'s website: <quote>)")
        pairs = [(f"From {e}'s website: {quote[:1000]}", hyp)
                 for e, quote, hyp in zip(rows["Entity"],
                                          rows["Verbatim Quote"],
                                          rows["hypothesis"])]
    else:
        pairs = [(quote[:1000], hyp) for quote, hyp in
                 zip(rows["Verbatim Quote"], rows["hypothesis"])]
    logits = model.predict(pairs, batch_size=32, show_progress_bar=False,
                           apply_softmax=True)
    probs = np.asarray(logits)
    for i, label in id2label.items():
        rows[f"nli_{label}"] = probs[:, i]
    # support = P(entailment) for affirmative claims, P(contradiction) for
    # binary-No claims (the affirmative hypothesis being contradicted IS the
    # support for "No").
    ent_col = next(c for c in rows.columns if c.startswith("nli_ent"))
    con_col = next(c for c in rows.columns if c.startswith("nli_con"))
    rows["support"] = [
        r[con_col] if r["polarity"] == "contradiction" else r[ent_col]
        for _, r in rows.iterrows()
    ]
    # Flagging bands only — thresholds UNCALIBRATED (semantic-verify
    # contract: annotate, never silently gate).
    rows["support_band"] = pd.cut(
        rows["support"], [-0.001, 0.3, 0.7, 1.001],
        labels=["unsupported", "unclear", "supported"])

    print("\n===== support distribution =====")
    print(rows["support"].describe().to_string())
    print("\nband counts:")
    print(rows["support_band"].value_counts().to_string())

    print("\n===== per-question support =====")
    per_q = rows.groupby("Question")["support"].agg(["count", "min", "median", "max"])
    for q, r in per_q.sort_values("median").iterrows():
        print(f"  n={int(r['count']):3d}  min={r['min']:.3f}  med={r['median']:.3f}  "
              f"max={r['max']:.3f}  {q[:70]}")

    # --- pre-registered exemplars -------------------------------------------
    print("\n===== pre-registered exemplars =====")
    ex2 = rows[(rows["Question"].str.contains("headquarters", case=False))
               & rows["Verbatim Quote"].str.contains("Seestrasse", na=False)]
    for _, r in ex2.iterrows():
        print(f"  [expect HIGH] Tecan address: support={r['support']:.3f}  "
              f"({r['support_band']})")
    ex3 = rows[(rows["Question"].str.contains("EOL", case=False))
               & (rows["Verbatim Quote"].str.strip() == "Test Development")]
    for _, r in ex3.iterrows():
        print(f"  [expect LOW]  'Test Development' -> EOL Yes: "
              f"support={r['support']:.3f}  ({r['support_band']})")

    print("\n===== suspects: EOL-Yes + independence-Yes, sorted by support =====")
    suspects = rows[
        (rows["Question"].str.contains("EOL|operating independently",
                                       case=False, na=False))
        & rows["Claim"].str.strip().str.match(r"^yes\.?$", case=False)
    ].sort_values("support")
    for _, r in suspects.iterrows():
        print(f"  {r['support']:.3f} ({str(r['support_band']):11s}) "
              f"[{r['Claim ID']}] {r['Entity'][:20]:20s} "
              f"Q: {r['Question'][:40]:40s} quote: {str(r['Verbatim Quote'])[:55]}")

    print("\n===== bottom 12 overall (candidate unsupported claims) =====")
    for _, r in rows.nsmallest(12, "support").iterrows():
        print(f"  {r['support']:.3f}  [{r['Claim ID']}] {r['Entity'][:18]:18s} "
              f"Q: {r['Question'][:40]:40s} val: {str(r['Claim'])[:28]:28s} "
              f"quote: {str(r['Verbatim Quote'])[:50]}")

    print("\n===== top 8 overall (sanity: should be obviously-supported) =====")
    for _, r in rows.nlargest(8, "support").iterrows():
        print(f"  {r['support']:.3f}  [{r['Claim ID']}] {r['Entity'][:18]:18s} "
              f"Q: {r['Question'][:40]:40s} val: {str(r['Claim'])[:28]:28s} "
              f"quote: {str(r['Verbatim Quote'])[:50]}")

    if ns.output:
        rows.to_excel(ns.output, index=False)
        print(f"\nscores written: {ns.output}")


if __name__ == "__main__":
    main()
