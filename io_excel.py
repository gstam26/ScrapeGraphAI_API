import os
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

from models import ColumnSpec, PipelineResult


def read_urls_from_excel(filepath: str) -> list[str]:
    df = pd.read_excel(filepath)

    url_col = next(
        (col for col in df.columns if "url" in col.lower() or "link" in col.lower()),
        df.columns[0],
    )

    return df[url_col].dropna().astype(str).tolist()


def parse_columns(raw_columns: list[str]) -> list[ColumnSpec]:
    columns = []

    for raw in raw_columns:
        if ":" in raw:
            name, instruction = raw.split(":", 1)
            columns.append(ColumnSpec(name=name.strip(), instruction=instruction.strip()))
        else:
            columns.append(ColumnSpec(name=raw.strip()))

    return columns


def format_value(value):
    if value is None:
        return ""

    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if item not in (None, "")]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        return "\n".join(f"• {item}" for item in cleaned)

    return value


def write_output_excel(result: PipelineResult, columns: list[ColumnSpec], output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    matrix_rows = []
    provenance_rows = []

    for row in result.rows:
        matrix_row = {"URL": row.url}

        for col in columns:
            matching_cell = next(
                (cell for cell in row.cells if cell.column == col.name),
                None,
            )

            if matching_cell:
                matrix_row[col.name] = format_value(matching_cell.value)

                provenance_rows.append({
                    "URL": matching_cell.url,
                    "Column": matching_cell.column,
                    "Value": format_value(matching_cell.value),
                    "Quote": matching_cell.quote or "",
                    "Verified": matching_cell.verified,
                    "Verification score": matching_cell.verification_score,
                })
            else:
                matrix_row[col.name] = ""

        matrix_rows.append(matrix_row)

    matrix_df = pd.DataFrame(matrix_rows, columns=["URL"] + [c.name for c in columns])
    provenance_df = pd.DataFrame(provenance_rows)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        matrix_df.to_excel(writer, sheet_name="Matrix", index=False)
        provenance_df.to_excel(writer, sheet_name="Provenance", index=False)

    _format_workbook(output_path)


def _format_workbook(output_path: str):
    wb = load_workbook(output_path)

    header_font = Font(name="Arial", bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", start_color="2E4057")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    body_alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)

    for ws in wb.worksheets:
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment

        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.font = Font(name="Arial", size=10)
                cell.alignment = body_alignment

        for column_cells in ws.columns:
            letter = column_cells[0].column_letter
            ws.column_dimensions[letter].width = 35

        ws.freeze_panes = "A2"

    wb.save(output_path)