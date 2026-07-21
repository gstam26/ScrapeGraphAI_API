"""
Matcher validation harness — does the generic evaluator's matching agree
with a human?

The generic evaluator's numbers are only as trustworthy as its match
thresholds (MATCH_THRESHOLD / REVIEW_THRESHOLD / semantic floor), and none
of them has ever been checked against human judgment. This harness closes
that gap with the same two-step pattern the summary layer used for its
judge (diagnostics/summary_eval.py label-template / label-score):

  label-template  — run the alignment on a real GT + pipeline output pair
                    and write every (gt_value, ai_value) pair the matcher
                    decided on into a labelling workbook: matched pairs
                    (matcher says SAME) and, for each missed GT claim, its
                    lexically-closest leftover AI claims (matcher says
                    DIFFERENT). The human fills one column: SAME or
                    DIFFERENT.

  label-score     — read the filled workbook and report agreement between
                    matcher and human, overall and per verdict band, plus
                    the confusion counts. Pre-registered bar: >= 0.80
                    agreement (the same bar the summary judge had to meet).
                    Below the bar, the evaluator's headline numbers should
                    be reported with a matcher-uncertainty caveat, and the
                    thresholds recalibrated on the labelled pairs.

Usage:
  python src/eval/matcher_eval.py label-template <gt.xlsx> <output.xlsx> \
      --output matcher_labels.xlsx [--sheet matrix] [--no-semantic]
  python src/eval/matcher_eval.py label-score matcher_labels.xlsx
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
from rapidfuzz import fuzz

from src.eval.generic_eval import (
    evaluate,
    read_gt,
    read_pipeline_matrix,
    read_pipeline_output,
    _norm,
)

AGREEMENT_BAR = 0.80

# Max closest leftover AI claims paired with each missed GT claim in the
# template. More adds labelling volume without adding signal — the nearest
# neighbours are the informative near-misses.
MISS_NEIGHBOURS = 2

_LABEL_COLUMNS = [
    "entity", "question", "is_list", "gt_value", "ai_value",
    "lexical", "semantic", "combined", "matcher_verdict", "matcher_says",
    "human_label", "notes",
]


# ---------------------------------------------------------------------------
# label-template
# ---------------------------------------------------------------------------
def build_template_rows(result) -> list[dict]:
    """Flatten an EvalResult into labelling rows.

    Matched pairs (auto_match / review / semantic_review) are the matcher's
    SAME decisions. For each missed GT claim, its MISS_NEIGHBOURS closest
    ai_only claims (lexical) are the matcher's informative DIFFERENT
    decisions — the near-misses a human should double-check. Distant misses
    are not worth labelling time.
    """
    rows: list[dict] = []
    for cell in result.cells:
        for p in cell.gt_pairs:
            if p.verdict in ("auto_match", "review", "semantic_review"):
                rows.append({
                    "entity": cell.entity, "question": cell.question,
                    "is_list": cell.is_list,
                    "gt_value": p.gt_value, "ai_value": p.ai_value,
                    "lexical": p.value_score, "semantic": p.semantic,
                    "combined": p.combined, "matcher_verdict": p.verdict,
                    "matcher_says": "SAME",
                    "human_label": "", "notes": "",
                })
            elif p.verdict == "auto_miss" and cell.ai_only:
                scored = sorted(
                    ((fuzz.token_sort_ratio(_norm(p.gt_value), _norm(a.value)) / 100.0, a)
                     for a in cell.ai_only),
                    key=lambda t: t[0], reverse=True,
                )
                for lex, a in scored[:MISS_NEIGHBOURS]:
                    rows.append({
                        "entity": cell.entity, "question": cell.question,
                        "is_list": cell.is_list,
                        "gt_value": p.gt_value, "ai_value": a.value,
                        "lexical": round(lex, 4), "semantic": 0.0,
                        "combined": round(lex, 4), "matcher_verdict": "auto_miss",
                        "matcher_says": "DIFFERENT",
                        "human_label": "", "notes": "",
                    })
    return rows


def write_template(rows: list[dict], out_path: str) -> None:
    df = pd.DataFrame(rows, columns=_LABEL_COLUMNS)
    instructions = pd.DataFrame(
        [("What this is",
          "Each row is one (ground-truth value, AI value) pair the evaluator "
          "decided on. matcher_says is its decision."),
         ("Your task",
          "Fill human_label with SAME (the two values state the same fact) "
          "or DIFFERENT (they do not). Leave blank to skip a row."),
         ("SAME means",
          "Same fact, any phrasing: 'Geneva' vs 'based in Geneva, "
          "Switzerland' is SAME. A country vs a different country is "
          "DIFFERENT. More/less detail is still SAME if not contradictory."),
         ("Then run",
          "python src/eval/matcher_eval.py label-score <this file>")],
        columns=["key", "value"],
    )
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Pairs", index=False)
        instructions.to_excel(w, sheet_name="Instructions", index=False)


# ---------------------------------------------------------------------------
# label-score
# ---------------------------------------------------------------------------
def score_labels(df: pd.DataFrame) -> dict:
    """Agreement between matcher_says and human_label on labelled rows."""
    labelled = df[df["human_label"].astype(str).str.strip().str.upper().isin(
        ["SAME", "DIFFERENT"])].copy()
    if labelled.empty:
        raise ValueError("No labelled rows (human_label must be SAME or DIFFERENT).")
    labelled["human"] = labelled["human_label"].astype(str).str.strip().str.upper()
    labelled["matcher"] = labelled["matcher_says"].astype(str).str.strip().str.upper()
    labelled["agree"] = labelled["human"] == labelled["matcher"]

    per_band = {}
    for band, sub in labelled.groupby("matcher_verdict"):
        per_band[str(band)] = {
            "n": int(len(sub)),
            "agreement": round(float(sub["agree"].mean()), 4),
        }

    confusion = {
        "matcher_same_human_same": int(((labelled["matcher"] == "SAME") & (labelled["human"] == "SAME")).sum()),
        "matcher_same_human_diff": int(((labelled["matcher"] == "SAME") & (labelled["human"] == "DIFFERENT")).sum()),
        "matcher_diff_human_same": int(((labelled["matcher"] == "DIFFERENT") & (labelled["human"] == "SAME")).sum()),
        "matcher_diff_human_diff": int(((labelled["matcher"] == "DIFFERENT") & (labelled["human"] == "DIFFERENT")).sum()),
    }

    agreement = round(float(labelled["agree"].mean()), 4)
    return {
        "n_labelled": int(len(labelled)),
        "agreement": agreement,
        "passed_bar": agreement >= AGREEMENT_BAR,
        "bar": AGREEMENT_BAR,
        "per_band": per_band,
        "confusion": confusion,
    }


def print_score_report(report: dict) -> None:
    print()
    print("=" * 60)
    print(" MATCHER vs HUMAN LABELS")
    print("=" * 60)
    print(f"  labelled pairs : {report['n_labelled']}")
    print(f"  agreement      : {report['agreement']:.3f}  "
          f"(bar {report['bar']:.2f} -> {'PASS' if report['passed_bar'] else 'FAIL'})")
    print("\n  per verdict band:")
    for band, m in sorted(report["per_band"].items()):
        print(f"    {band:<16} n={m['n']:<4} agreement={m['agreement']:.3f}")
    c = report["confusion"]
    print("\n  confusion (matcher / human):")
    print(f"    SAME/SAME      {c['matcher_same_human_same']:>4}   "
          f"SAME/DIFF      {c['matcher_same_human_diff']:>4}  <- matcher over-credits")
    print(f"    DIFF/SAME      {c['matcher_diff_human_same']:>4}   "
          f"DIFF/DIFF      {c['matcher_diff_human_diff']:>4}")
    print(f"                          ^- matcher misses real matches")
    if not report["passed_bar"]:
        print("\n  BELOW BAR: report evaluator headlines with a matcher-"
              "uncertainty caveat;")
        print("  recalibrate thresholds on these labelled pairs before "
              "trusting the numbers.")
    print()


# ---------------------------------------------------------------------------
# ce-rescore: score the SAME labelled pairs with the cross-encoder
# ---------------------------------------------------------------------------
def ce_rescore(df: pd.DataFrame, scorer=None) -> dict:
    """Head-to-head on human-labelled pairs: production matcher vs the
    cross-encoder scoring (gt_value, ai_value) directly.

    Also sweeps the CE threshold over the labelled pairs — CROSS_ENCODER_MIN
    (0.50) is a placeholder, and these labels are exactly the data that
    calibrates it. Pass `scorer=` to inject a fake model in tests."""
    if scorer is None:
        from src.eval.cross_encoder import CrossEncoderScorer
        scorer = CrossEncoderScorer()
        scorer.ensure_ready()

    labelled = df[df["human_label"].astype(str).str.strip().str.upper().isin(
        ["SAME", "DIFFERENT"])].copy()
    if labelled.empty:
        raise ValueError("No labelled rows (human_label must be SAME or DIFFERENT).")
    labelled["human"] = labelled["human_label"].astype(str).str.strip().str.upper()
    labelled["matcher"] = labelled["matcher_says"].astype(str).str.strip().str.upper()

    pairs = [(str(g), str(a)) for g, a in
             zip(labelled["gt_value"], labelled["ai_value"])]
    labelled["ce_score"] = scorer.score_pairs(pairs)

    matcher_agreement = float((labelled["matcher"] == labelled["human"]).mean())

    sweep = []
    for t in [round(0.05 * i, 2) for i in range(1, 20)]:
        ce_says = labelled["ce_score"].map(
            lambda s, t=t: "SAME" if s >= t else "DIFFERENT")
        sweep.append({"threshold": t,
                      "agreement": round(float((ce_says == labelled["human"]).mean()), 4)})
    best = max(sweep, key=lambda r: r["agreement"])
    at_default = next(
        (r for r in sweep
         if abs(r["threshold"] - scorer.min_score) < 1e-9), None)

    return {
        "n_labelled": int(len(labelled)),
        "matcher_agreement": round(matcher_agreement, 4),
        "ce_agreement_at_default": at_default["agreement"] if at_default else None,
        "ce_default_threshold": scorer.min_score,
        "ce_best_threshold": best["threshold"],
        "ce_agreement_at_best": best["agreement"],
        "sweep": sweep,
        "scores": labelled[["entity", "question", "gt_value", "ai_value",
                            "human", "matcher", "ce_score"]],
    }


def print_ce_report(report: dict) -> None:
    print()
    print("=" * 60)
    print(" CROSS-ENCODER vs PRODUCTION MATCHER (on human labels)")
    print("=" * 60)
    print(f"  labelled pairs             : {report['n_labelled']}")
    print(f"  production matcher agreement: {report['matcher_agreement']:.3f}")
    print(f"  CE agreement @ default {report['ce_default_threshold']:.2f} : "
          f"{report['ce_agreement_at_default']:.3f}")
    print(f"  CE agreement @ best    {report['ce_best_threshold']:.2f} : "
          f"{report['ce_agreement_at_best']:.3f}")
    print("\n  threshold sweep (agreement):")
    for r in report["sweep"]:
        marker = "  <- best" if r["threshold"] == report["ce_best_threshold"] else ""
        print(f"    t={r['threshold']:.2f}  {r['agreement']:.3f}{marker}")

    sc = report["scores"]
    t = report["ce_default_threshold"]
    ce_wrong = sc[(sc["ce_score"] >= t) != (sc["human"] == "SAME")]
    if not ce_wrong.empty:
        print(f"\n  CE disagreements with human @ t={t:.2f}:")
        for _, r in ce_wrong.iterrows():
            print(f"    human={r['human']:<9} ce={r['ce_score']:.3f}  "
                  f"GT {str(r['gt_value'])[:40]!r} <-> AI {str(r['ai_value'])[:40]!r}")
    m_wrong = sc[sc["matcher"] != sc["human"]]
    if not m_wrong.empty:
        print("\n  production-matcher disagreements with human:")
        for _, r in m_wrong.iterrows():
            print(f"    human={r['human']:<9} matcher={r['matcher']:<9} "
                  f"GT {str(r['gt_value'])[:40]!r} <-> AI {str(r['ai_value'])[:40]!r}")

    print("\n  NOTE: best threshold is chosen ON these labels — treat it as an")
    print("  estimate to re-check on a second label set, not a validated value.")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Validate the generic evaluator's matcher against human labels."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("label-template",
                       help="Write a labelling workbook from a GT + output pair")
    t.add_argument("ground_truth")
    t.add_argument("pipeline_output")
    t.add_argument("--output", required=True, help="Labelling workbook path")
    t.add_argument("--sheet", choices=["provenance", "matrix"], default="provenance")
    t.add_argument("--no-semantic", action="store_true")
    t.add_argument("--semantic-backend", choices=["ollama", "cross-encoder"],
                   default="ollama")

    s = sub.add_parser("label-score", help="Score a filled labelling workbook")
    s.add_argument("labels", help="Labelling workbook with human_label filled")

    c = sub.add_parser("ce-rescore",
                       help="Score the same labelled pairs with the cross-encoder "
                            "(needs local model files) and compare matchers")
    c.add_argument("labels", help="Labelling workbook with human_label filled")

    args = ap.parse_args()

    if args.cmd == "label-template":
        gt = read_gt(args.ground_truth)
        if args.sheet == "matrix":
            ai = read_pipeline_matrix(args.pipeline_output)
        else:
            ai = read_pipeline_output(args.pipeline_output)
        result = evaluate(gt, ai, semantic=not args.no_semantic,
                          semantic_backend=args.semantic_backend)
        rows = build_template_rows(result)
        if not rows:
            sys.exit("No labelable pairs found — did the alignment produce any matches?")
        write_template(rows, args.output)
        same = sum(1 for r in rows if r["matcher_says"] == "SAME")
        print(f"Wrote {args.output}: {len(rows)} pairs "
              f"({same} matcher-SAME, {len(rows) - same} matcher-DIFFERENT)")
        print("Fill the human_label column (SAME/DIFFERENT), then run:")
        print(f"  python src/eval/matcher_eval.py label-score {args.output}")
        return 0

    df = pd.read_excel(args.labels, sheet_name="Pairs")
    if args.cmd == "ce-rescore":
        report = ce_rescore(df)
        print_ce_report(report)
        return 0
    report = score_labels(df)
    print_score_report(report)
    return 0 if report["passed_bar"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
