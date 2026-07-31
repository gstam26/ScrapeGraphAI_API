"""Build a blank input workbook a user can fill in and run.

    python scripts/build_input_template.py my_project.xlsx

Writes the four-sheet input format (entities / urls / questions / config)
with one worked example row per sheet and an Instructions tab. The example
rows are real and runnable — replace them with your own companies.
See docs/QUICKSTART.md for the walkthrough.
"""
import sys

import openpyxl
from openpyxl.styles import Font, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="E8EAF6")
BOLD = Font(bold=True)

INSTRUCTIONS = [
    ("How to fill in this workbook", BOLD),
    ("", None),
    ("entities sheet: one company name per row, exactly as you want it "
     "to appear in the output.", None),
    ("urls sheet: the company website (full https:// address), a crawl "
     "depth (1 recommended), and the entity name it belongs to.", None),
    ("questions sheet: one question per row. The optional instructions "
     "column controls answer format - e.g. 'Answer Yes, No, or Not "
     "disclosed.' The output follows it.", None),
    ("config sheet: optional. Delete any row to use the default.", None),
    ("", None),
    ("Then run:  python main.py --input <this file> --output results.xlsx",
     BOLD),
]

CONFIG_ROWS = [
    ("CRAWL_MAX_PAGES", 40, "page budget per company (default 2 is for smoke tests)"),
    ("CRAWL_SCOPE", "host", "'site' also crawls the company's own subdomains"),
    ("CRAWL_RENDER_FOR_DISCOVERY", "false", "'true' helps sites whose menus need JavaScript"),
    ("EXTRACT_TOOL", "azure", "LLM backend for extraction"),
]


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "input_template.xlsx"
    if not out.endswith(".xlsx"):
        out += ".xlsx"

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("Instructions")
    for text, font in INSTRUCTIONS:
        ws.append([text])
        if font:
            ws.cell(row=ws.max_row, column=1).font = font
    ws.column_dimensions["A"].width = 90

    ws = wb.create_sheet("entities")
    ws.append(["entity"])
    ws.append(["Example Company Ltd"])

    ws = wb.create_sheet("urls")
    ws.append(["url", "depth", "entities"])
    ws.append(["https://www.example.com", 1, "Example Company Ltd"])

    ws = wb.create_sheet("questions")
    ws.append(["question", "instructions"])
    ws.append(["Where is the company headquarters located?",
               "City and country."])
    ws.append(["Does the company have ISO 13485 certification?",
               "Answer Yes, No, or Not disclosed. Answer No only if the "
               "site states it explicitly."])

    ws = wb.create_sheet("config")
    ws.append(["setting", "value", "note (not read by the tool)"])
    for row in CONFIG_ROWS:
        ws.append(row)

    for ws in wb.worksheets:
        for cell in ws[1]:
            if cell.value:
                cell.font = BOLD
                cell.fill = HEADER_FILL
        for col in ("A", "B", "C"):
            if ws.title != "Instructions":
                ws.column_dimensions[col].width = 44 if col == "A" else 28

    wb.save(out)
    print(f"Template written: {out}")
    print("Fill in the entities, urls and questions sheets, then run:")
    print(f"  python main.py --input {out} --output results.xlsx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
