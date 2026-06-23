"""
Eval extraction — end-to-end CLI for the Stage 10 / RQ3 framework.

Runs: read GT + pipeline output -> align -> (optionally apply manual review) ->
compute metrics -> write the six-sheet Excel report + print a console summary.

Usage:
    # Preliminary pass (writes report with a blank Manual Verdict column):
    python diagnostics/eval_lib/eval_extraction.py \
        --gt diagnostics/eval_lib/fixtures/ground_truth.xlsx \
        --pipeline diagnostics/eval_lib/fixtures/pipeline_output.xlsx \
        --out outputs/eval_report.xlsx

    # Final pass (analyst has filled Manual Verdict in the prior report):
    python diagnostics/eval_lib/eval_extraction.py \
        --gt ... --pipeline ... --review outputs/eval_report.xlsx \
        --out outputs/eval_report_final.xlsx

Needs Ollama running for the Sustainability cosine path (same as the aligner).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from diagnostics.eval_lib.aligner import align
from diagnostics.eval_lib.gt_reader import read_ground_truth
from diagnostics.eval_lib.metrics import compute_metrics
from diagnostics.eval_lib.pipeline_reader import read_pipeline_output
from diagnostics.eval_lib.report_writer import (
    apply_manual_verdicts,
    read_manual_verdicts,
    write_report,
)


def _fmt(g) -> str:
    cos = f"{g.avg_match_cosine:.2f}" if g.avg_match_cosine is not None else "  - "
    return (f"R_auto={g.recall_auto:.2f} R_full={g.recall_full:.2f} | "
            f"P_strict={g.precision_strict:.2f} P_dist={g.precision_distinct:.2f} | "
            f"F1={g.f1_distinct:.2f} | cos={cos} | "
            f"gap={g.possible_gt_gap} halluc={g.hallucinations} redund={g.redundant_restatements}")


def run(gt_path: str, pipe_path: str, out_path: str, review_path: str | None) -> None:
    gt = read_ground_truth(gt_path)
    pipe = read_pipeline_output(pipe_path)
    result = align(gt, pipe)

    if review_path:
        verdicts = read_manual_verdicts(review_path)
        result = apply_manual_verdicts(result, verdicts)
        print(f"  applied {len(verdicts)} manual verdict(s) from {review_path}")

    report = compute_metrics(result, gt)
    write_report(result, report, gt, out_path)

    print(f"\n=== eval extraction {'(FINAL)' if review_path else '(preliminary)'} ===")
    print(f"  GT       : {gt_path}")
    print(f"  pipeline : {pipe_path}")
    print(f"  report   : {out_path}\n")
    print("--- by question ---")
    for g in report.by_question:
        print(f"  {g.label:16} ({g.n_cells:2} cells, {g.gt_active:3} GT): {_fmt(g)}")
    print("\n--- overall ---")
    print(f"  {_fmt(report.overall)}")
    print(f"\n  ground-truth completeness: {report.overall.possible_gt_gap} possible GT-gaps "
          f"vs {report.overall.hallucinations} true hallucinations.")
    if not review_path:
        man = sum(c.manual_band for c in report.cells)
        print(f"  manual-review queue: {man} GT rows await a verdict "
              f"(fill 'Manual Verdict' in the report, then re-run with --review).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 10 extraction evaluation (end-to-end).")
    ap.add_argument("--gt", required=True, help="ground-truth workbook")
    ap.add_argument("--pipeline", required=True, help="pipeline output workbook (Matrix + Provenance)")
    ap.add_argument("--out", default="", help="output report path (.xlsx)")
    ap.add_argument("--review", default="", help="a filled report to read Manual Verdict from (final pass)")
    args = ap.parse_args()

    out = args.out
    if not out:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        suffix = "_final" if args.review else ""
        out = os.path.join("outputs", f"eval_report_{stamp}{suffix}.xlsx")

    run(args.gt, args.pipeline, out, args.review or None)


if __name__ == "__main__":
    main()
