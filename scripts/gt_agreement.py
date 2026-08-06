"""Inter-annotator agreement between two FLAT ground-truth workbooks.

Reuses generic_eval's own alignment + CE matcher end to end — one analyst's
converted GT plays the "pipeline" side (GTRow -> AIRow adapter below), so
agreement is measured by EXACTLY the matcher that scores the real pipeline.
No new matching logic; no changes to scoring logic.

Run BOTH directions (done automatically): recall(A->B) = share of A's claims
B also recorded; recall(B->A) the reverse. On single-answer questions the
two directions' F1 should be close — a big gap means one analyst records
facts the other leaves blank (coverage difference, not disagreement). On
list questions expect asymmetry (non-exhaustive fills).

Interpretation guide printed with the numbers:
  TP = claims both analysts recorded (matcher says same fact)
  FN = claims only the GT-side analyst recorded
  FP = claims only the AI-side analyst recorded
  "hallucination_rate" here = share of the AI-side analyst's claims the
  other analyst does not have — coverage gap, not error.

Usage:
  python scripts/gt_agreement.py gt_analystA.xlsx gt_analystB.xlsx
  python scripts/gt_agreement.py a.xlsx b.xlsx --prose-cells
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.eval.generic_eval import AIRow, evaluate, read_gt  # noqa: E402


def _as_ai(rows) -> list[AIRow]:
    """Adapt one analyst's GT rows to the evaluator's pipeline-side shape.
    verified=True: an analyst claim is not an unverified extraction."""
    return [AIRow(entity=r.entity, entity_norm=r.entity_norm,
                  question=r.question, question_norm=r.question_norm,
                  value=r.value, quote=r.verbatim_quote, verified=True,
                  match_type="analyst", source_url=r.source_url)
            for r in rows]


def _direction(name: str, gt_rows, ai_rows, backend: str,
               prose_cells: bool) -> dict:
    print(f"\n--- direction {name} ---")
    result = evaluate(gt_rows, _as_ai(ai_rows), semantic=True,
                      semantic_backend=backend, prose_cells=prose_cells)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Inter-annotator agreement via generic_eval's matcher.")
    ap.add_argument("gt_a", help="Flat GT workbook, analyst A (post gt_convert)")
    ap.add_argument("gt_b", help="Flat GT workbook, analyst B (post gt_convert)")
    ap.add_argument("--semantic-backend", choices=["ollama", "cross-encoder"],
                    default="cross-encoder")
    ap.add_argument("--prose-cells", action="store_true",
                    help="Same grain option as generic_eval; use the SAME "
                         "setting as your pipeline scoring runs")
    args = ap.parse_args()

    a, b = read_gt(args.gt_a), read_gt(args.gt_b)
    ents_a = {r.entity_norm: r.entity for r in a}
    ents_b = {r.entity_norm: r.entity for r in b}
    shared = sorted(set(ents_a) & set(ents_b))
    if not shared:
        sys.exit("No overlapping entities between the two GT files — "
                 "agreement needs both analysts to have filled the same rows.")
    print(f"Overlap: {len(shared)} entities "
          f"(A has {len(ents_a)}, B has {len(ents_b)}): "
          f"{[ents_a[e] for e in shared]}")
    only_a = {r.question for r in a} - {r.question for r in b}
    only_b = {r.question for r in b} - {r.question for r in a}
    for label, qs in (("A", only_a), ("B", only_b)):
        if qs:
            print(f"  !! questions only in {label} (excluded from agreement "
                  f"by alignment): {sorted(qs)}")

    a_shared = [r for r in a if r.entity_norm in set(shared)]
    b_shared = [r for r in b if r.entity_norm in set(shared)]

    r_ab = _direction("A=GT vs B=claims", a_shared, b_shared,
                      args.semantic_backend, args.prose_cells)
    r_ba = _direction("B=GT vs A=claims", b_shared, a_shared,
                      args.semantic_backend, args.prose_cells)

    print("\n" + "=" * 72)
    print(" INTER-ANNOTATOR AGREEMENT "
          f"({os.path.basename(args.gt_a)} vs {os.path.basename(args.gt_b)})")
    print("=" * 72)
    print(f"{'QUESTION':<42} {'F1 A->B':>8} {'F1 B->A':>8}  note")
    print("-" * 72)
    qs = sorted(set(r_ab.per_question) | set(r_ba.per_question))
    for q in qs:
        ma = r_ab.per_question.get(q, {})
        mb = r_ba.per_question.get(q, {})
        fa, fb = ma.get("F1"), mb.get("F1")
        gap = (abs(fa - fb) if fa is not None and fb is not None else 0.0)
        note = "COVERAGE GAP" if gap >= 0.2 else ""
        fa_s = f"{fa:.3f}" if fa is not None else "  -  "
        fb_s = f"{fb:.3f}" if fb is not None else "  -  "
        print(f"{q[:42]:<42} {fa_s:>8} {fb_s:>8}  {note}")
    oa, ob = r_ab.overall, r_ba.overall
    print("-" * 72)
    print(f"{'OVERALL':<42} {oa['F1']:>8.3f} {ob['F1']:>8.3f}")
    print(f"  A->B: TP={oa['TP']} FN={oa['FN']} FP={oa['FP']} "
          f"P={oa['precision']:.3f} R={oa['recall']:.3f}")
    print(f"  B->A: TP={ob['TP']} FN={ob['FN']} FP={ob['FP']} "
          f"P={ob['precision']:.3f} R={ob['recall']:.3f}")
    print("\n  FN(A->B) = A-only claims; FP(A->B) = B-only claims. "
          "Symmetric-ish F1 with low overall = true disagreement; "
          "asymmetric F1 = coverage difference. List questions: expect "
          "asymmetry (non-exhaustive fills).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
