"""One-command CMO scoring: pipeline workbook in, styled eval report out.

    python scripts/score_cmo.py outputs/<run>.xlsx
    python scripts/score_cmo.py outputs/<run>.xlsx --gt outputs/cmo_gt_final59.xlsx

Wraps generic_eval with the decided HEADLINE configuration — deliverable
grain (--sheet matrix), prose cells joined (--prose-cells), CE matcher — so
final numbers are always produced the same way, and stamps a Run Info sheet
(GT version, flags, matcher backend, date) into the report so no scoring is
ever ambiguous about how it was made.

Presentation: the report is styled for reading — frozen headers, wrapped
text, verdict colour-coding, autofilter — without touching a single scored
number (write_report_excel's data is written first, styling is applied to
the saved file afterwards).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_GT = os.path.join("outputs", "cmo_gt_final59.xlsx")

# Verdict fills — muted so text stays readable when printed.
_VERDICT_FILL = {
    "auto_match":      "C6EFCE",  # green
    "review":          "C6EFCE",
    "semantic_review": "DDEBF7",  # blue — CE-only credit, auditable
    "null_match":      "E2EFDA",  # pale green — correct abstention
    "auto_miss":       "FFC7CE",  # red — missed GT
    "no_ai_data":      "FFC7CE",
    "ai_only":         "FFE699",  # amber — unmatched claim (FP)
    "redundant":       "EDEDED",  # grey — restatement, precision-exempt
    "suppressed_null": "EDEDED",  # grey — abstention, precision-exempt
}


def style_report(path: str, meta_rows: list[tuple[str, str]]) -> None:
    """Post-style the written report + prepend a Run Info sheet."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.load_workbook(path)
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="44546A")
    wrap = Alignment(wrap_text=True, vertical="top")

    # ── Run Info sheet, first position ────────────────────────────────────
    info = wb.create_sheet("Run Info", 0)
    info.append(["CMO evaluation report"])
    info["A1"].font = Font(bold=True, size=14)
    info.append([])
    for k, v in meta_rows:
        info.append([k, v])
        info.cell(row=info.max_row, column=1).font = Font(bold=True)
    info.column_dimensions["A"].width = 26
    info.column_dimensions["B"].width = 90
    for row in info.iter_rows(min_row=3):
        row[1].alignment = wrap

    # ── Summary: headers, widths, percent-ish rounding, freeze ────────────
    ws = wb["Summary"]
    for c in ws[1]:
        c.font, c.fill = head_font, head_fill
    ws.freeze_panes = "B2"
    ws.column_dimensions["A"].width = 62
    for col in range(2, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(col)].width = 12
    for row in ws.iter_rows(min_row=2):
        row[0].alignment = wrap
        for c in row[5:9]:
            c.number_format = "0.000"
        if str(row[0].value) == "OVERALL":
            for c in row:
                c.font = Font(bold=True)
    ws.auto_filter.ref = ws.dimensions

    # ── Detail: freeze entity+question, wrap the claim text, colour verdicts ──
    ws = wb["Detail"]
    hdr = [str(c.value) for c in ws[1]]
    for c in ws[1]:
        c.font, c.fill = head_font, head_fill
    ws.freeze_panes = "C2"
    widths = {"entity": 24, "question": 42, "gt_value": 46, "ai_value": 46,
              "is_list": 8, "value_score": 11, "quote_score": 11,
              "semantic": 11, "combined": 11, "verdict": 16}
    v_idx = hdr.index("verdict")
    for i, name in enumerate(hdr, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(name, 14)
    for row in ws.iter_rows(min_row=2):
        for name, c in zip(hdr, row):
            if name in ("entity", "question", "gt_value", "ai_value"):
                c.alignment = wrap
        fill = _VERDICT_FILL.get(str(row[v_idx].value))
        if fill:
            row[v_idx].fill = PatternFill("solid", fgColor=fill)
    ws.auto_filter.ref = ws.dimensions

    wb.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Score a CMO pipeline workbook against the final GT "
                    "(headline config: matrix sheet, prose cells, CE matcher).")
    ap.add_argument("pipeline_output", help="Pipeline output workbook (.xlsx)")
    ap.add_argument("--gt", default=DEFAULT_GT,
                    help=f"Flat GT workbook (default: {DEFAULT_GT})")
    ap.add_argument("--output", default=None,
                    help="Report path (default: outputs/eval_<run-name>_<date>.xlsx)")
    args = ap.parse_args()

    if not os.path.exists(args.gt):
        sys.exit(f"GT not found: {args.gt} (pull, or pass --gt)")
    if not os.path.exists(args.pipeline_output):
        sys.exit(f"Pipeline workbook not found: {args.pipeline_output}")

    run_name = os.path.splitext(os.path.basename(args.pipeline_output))[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = args.output or os.path.join("outputs", f"eval_{run_name}_{stamp}.xlsx")

    from src.eval.generic_eval import (evaluate, print_report, read_gt,
                                       read_pipeline_matrix, read_run_entities,
                                       write_report_excel)

    gt = read_gt(args.gt)
    ai = read_pipeline_matrix(args.pipeline_output)
    run_ents = read_run_entities(args.pipeline_output)
    result = evaluate(gt, ai, semantic=True, semantic_backend="cross-encoder",
                      run_entities=run_ents, prose_cells=True)
    print_report(result)
    write_report_excel(result, out)

    o = result.overall
    style_report(out, [
        ("Pipeline run", os.path.basename(args.pipeline_output)),
        ("Ground truth", f"{os.path.basename(args.gt)}  "
                         f"({o['entities']} entities scored, {len(gt)} GT rows)"),
        ("Scored on", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Configuration", "sheet=matrix (deliverable grain), prose-cells=on, "
                          "semantic-backend=cross-encoder (decisive)"),
        ("Headline", f"P={o['precision']:.3f}  R={o['recall']:.3f}  "
                     f"F1={o['F1']:.3f}  hallucination={o['hallucination_rate']:.3f}"),
        ("Reading guide", "List-question precision is a LOWER BOUND (GT is "
                          "non-exhaustive): unmatched items may be real but "
                          "unlisted. Grey Detail rows are precision-exempt "
                          "(restatements / abstentions). Blue rows are "
                          "matches credited by the semantic matcher alone."),
    ])
    print(f"Styled report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
