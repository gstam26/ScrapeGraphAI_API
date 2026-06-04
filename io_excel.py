import os
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

from models import ColumnSpec, PipelineResult


def read_urls_from_excel(filepath: str) -> list[str]:
    """Read entity URLs from input Excel file."""
    df = pd.read_excel(filepath)

    url_col = next(
        (col for col in df.columns if "url" in col.lower() or "link" in col.lower()),
        df.columns[0],
    )

    return df[url_col].dropna().astype(str).tolist()


def parse_columns(raw_columns: list[str]) -> list[ColumnSpec]:
    """Parse column specs from user input."""
    columns = []

    for raw in raw_columns:
        if ":" in raw:
            name, instruction = raw.split(":", 1)
            columns.append(ColumnSpec(name=name.strip(), instruction=instruction.strip()))
        else:
            columns.append(ColumnSpec(name=raw.strip()))

    return columns


def _format_value(value):
    """Format a cell value for display."""
    if value is None:
        return ""

    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if item not in (None, "")]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        return "\n".join(f"• {item}" for item in cleaned)

    return str(value)


def write_output_excel(result: PipelineResult, columns: list[ColumnSpec], output_path: str):
    """Write results to Excel with Matrix and Provenance sheets."""
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    matrix_rows = []
    provenance_rows = []

    for row in result.rows:
        matrix_row = {"Entity URL": row.entity_url}

        for col in columns:
            matching_cells = [cell for cell in row.cells if cell.column == col.name]

            if matching_cells:
                # Use first cell's value for matrix
                cell = matching_cells[0]
                matrix_row[col.name] = _format_value(cell.value)

                # Create provenance rows: one per evidence item
                for cell in matching_cells:
                    for evidence in cell.evidence:
                        provenance_rows.append({
                            "Entity URL": row.entity_url,
                            "Source URL": cell.source_url,
                            "Column": col.name,
                            "Item": _format_value(evidence.value),
                            "Value": _format_value(evidence.value),
                            "Quote": evidence.quote or "",
                            "Verified": evidence.verified,
                            "Verification score": evidence.verification_score or "",
                        })

                    # If no evidence but cell has value, create provenance row anyway
                    if not cell.evidence and cell.value is not None:
                        provenance_rows.append({
                            "Entity URL": row.entity_url,
                            "Source URL": cell.source_url,
                            "Column": col.name,
                            "Item": _format_value(cell.value),
                            "Value": _format_value(cell.value),
                            "Quote": "",
                            "Verified": cell.verified,
                            "Verification score": cell.verification_score or "",
                        })
            else:
                matrix_row[col.name] = ""

        matrix_rows.append(matrix_row)

    # Create DataFrames
    matrix_df = pd.DataFrame(
        matrix_rows,
        columns=["Entity URL"] + [c.name for c in columns]
    )

    if provenance_rows:
        provenance_df = pd.DataFrame(provenance_rows)
        provenance_cols = [
            "Entity URL", "Source URL", "Column", "Item", "Value",
            "Quote", "Verified", "Verification score"
        ]
        # Reorder to match spec
        provenance_df = provenance_df[[col for col in provenance_cols if col in provenance_df.columns]]
    else:
        provenance_df = pd.DataFrame(columns=[
            "Entity URL", "Source URL", "Column", "Item", "Value",
            "Quote", "Verified", "Verification score"
        ])

    # Write to Excel
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        matrix_df.to_excel(writer, sheet_name="Matrix", index=False)
        provenance_df.to_excel(writer, sheet_name="Provenance", index=False)

    _format_workbook(output_path)


def _format_workbook(output_path: str):
    """Apply formatting to workbook."""
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
