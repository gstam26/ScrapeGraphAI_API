"""
Ground-truth template generator.

Reads the standard pipeline input workbook (entities, urls, questions sheets)
and produces an empty Excel template for manual ground-truth completion.

The Ground Truth sheet has one pre-populated row per (entity, question) pair
(two rows per pair as a hint that multiple claims are possible). Analysts fill
in Claim, Supporting Quote, Source URL, and Notes; Entity and Question are
shaded to signal they should not be edited.

Usage:
    python diagnostics/ground_truth_template.py samples/input3.xlsx
    python diagnostics/ground_truth_template.py samples/input3.xlsx --output outputs/my_gt.xlsx
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
from dotenv import load_dotenv
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

load_dotenv()

from src.io_excel import read_input

_HEADER_FILL = "2E4057"
_HEADER_FONT = "FFFFFF"
_ALT_ROW = "F5F5F5"
_PREPOP_FILL = "E8EAF6"  # light indigo for pre-populated (read-only intent) cells

_TAB_COLORS = {
    "Ground Truth": "4CAF50",
    "Instructions": "2196F3",
}

# Two blank rows per (entity, question) pair signal that multiple claims are possible.
_ROWS_PER_PAIR = 2


def _make_ground_truth_df(entities: list[str], questions: list[str]) -> pd.DataFrame:
    col_names = ["Entity", "Question", "Claim", "Supporting Quote", "Source URL", "Notes"]
    rows = []
    for entity in entities:
        for question in questions:
            for _ in range(_ROWS_PER_PAIR):
                rows.append({
                    "Entity": entity,
                    "Question": question,
                    "Claim": "",
                    "Supporting Quote": "",
                    "Source URL": "",
                    "Notes": "",
                })
    return pd.DataFrame(rows, columns=col_names) if rows else pd.DataFrame(columns=col_names)


def _make_instructions_df() -> pd.DataFrame:
    guidelines = [
        "One row per claim. If a question has multiple answers for one entity, add extra rows with the same Entity and Question values.",
        "Copy quotes verbatim from the source page — do not paraphrase. Paste the exact text into Supporting Quote.",
        "Source URL should match the page URL exactly as it appears in the pipeline's Acquire Log (e.g. https://example.com/page, no trailing slash changes).",
        "Entity and Question are pre-populated (shaded blue) — do not edit them. Only fill Claim, Supporting Quote, Source URL, and Notes.",
        "Notes is optional. Use it for ambiguous cases, conflicting sources, date caveats, or anything needing reviewer attention.",
        "Leave Claim blank if no answer was found for that entity/question pair on any source page.",
    ]
    return pd.DataFrame(
        [{"#": i + 1, "Guideline": g} for i, g in enumerate(guidelines)],
        columns=["#", "Guideline"],
    )


def _style_sheet(ws, tab_color: str, prepopulated_cols: set[int] | None = None) -> None:
    ws.sheet_properties.tabColor = tab_color
    ws.freeze_panes = "A2"

    h_fill = PatternFill("solid", fgColor=_HEADER_FILL)
    h_font = Font(name="Arial", bold=True, color=_HEADER_FONT, size=10)
    h_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for cell in ws[1]:
        cell.fill = h_fill
        cell.font = h_font
        cell.alignment = h_align
    ws.row_dimensions[1].height = 28

    b_font = Font(name="Arial", size=10)
    b_align = Alignment(horizontal="left", vertical="top", wrap_text=True)
    alt = PatternFill("solid", fgColor=_ALT_ROW)
    prepop = PatternFill("solid", fgColor=_PREPOP_FILL)

    for row_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
        for cell in row:
            cell.font = b_font
            cell.alignment = b_align
            if prepopulated_cols and cell.column in prepopulated_cols:
                cell.fill = prepop
            elif row_idx % 2 == 0:
                cell.fill = alt

    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            if cell.value is not None:
                longest = max((len(line) for line in str(cell.value).split("\n")), default=0)
                max_len = max(max_len, longest)
        ws.column_dimensions[letter].width = min(max(max_len + 2, 12), 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate empty ground-truth template workbook")
    parser.add_argument("input", help="Path to the pipeline input Excel file")
    parser.add_argument("--output", default="", help="Path for the output template (optional)")
    args = parser.parse_args()

    pipeline_input = read_input(args.input)
    entities = pipeline_input.entities
    questions = [col.name for col in pipeline_input.columns]

    if not entities:
        print("  No entities found in input — nothing to generate.")
        return
    if not questions:
        print("  No questions found in input — nothing to generate.")
        return

    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output_path = os.path.join("outputs", f"{base}_ground_truth_template.xlsx")

    print(f"\n  Ground Truth Template")
    print(f"  input     : {args.input}")
    print(f"  entities  : {len(entities)}")
    print(f"  questions : {len(questions)}")
    print(f"  rows      : {len(entities) * len(questions) * _ROWS_PER_PAIR}  ({_ROWS_PER_PAIR} per entity×question pair)")
    print(f"  output    : {output_path}\n")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    gt_df = _make_ground_truth_df(entities, questions)
    instr_df = _make_instructions_df()

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        gt_df.to_excel(writer, sheet_name="Ground Truth", index=False)
        instr_df.to_excel(writer, sheet_name="Instructions", index=False)

    wb = load_workbook(output_path)
    for ws in wb.worksheets:
        # Columns 1 (Entity) and 2 (Question) are pre-populated on the Ground Truth sheet.
        prepop_cols = {1, 2} if ws.title == "Ground Truth" else None
        _style_sheet(ws, _TAB_COLORS.get(ws.title, _HEADER_FILL), prepopulated_cols=prepop_cols)
    wb.save(output_path)

    print(f"  Template saved → {output_path}\n")


if __name__ == "__main__":
    main()
