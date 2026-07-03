"""
Traceability chain tests (Advisory requirement, 2026-07-03): every theme and
digest line must trace back to verified claims. Chain under test:

    Digest -> Grouped Themes -> Provenance (claim IDs + internal hyperlinks)
           -> source URL (external hyperlink)

Fully offline: builds a small PipelineResult + claim_groups by hand, writes a
real workbook to tmp_path, reads it back with openpyxl.
"""
import openpyxl

from models import ColumnSpec, ExtractedCell, ExtractedRow, PipelineResult, SourceQuote
from src.io_excel import write_output_excel

_URL = "https://example.com/news"
_CLAIMS = [
    "regulatory clearance for assay Z",
    "regulatory approval in Europe",
    "launch of product line A",
    "new product launch in Japan",
]


def _result() -> PipelineResult:
    evidence = [SourceQuote(value=c, quote=c, source_url=_URL, verified=True) for c in _CLAIMS]
    cell = ExtractedCell(entity="TestCo", source_url=_URL, column="Recent news",
                         value=list(_CLAIMS), evidence=evidence, verified=True)
    return PipelineResult(rows=[ExtractedRow(entity="TestCo", cells=[cell], all_cells=[cell])])


def _diag() -> dict:
    return {
        "claim_groups": [
            {"entity": "TestCo", "question": "Recent news",
             "theme": _CLAIMS[0], "n_items": 2, "values": _CLAIMS[:2], "sources": 1},
            {"entity": "TestCo", "question": "Recent news",
             "theme": _CLAIMS[2], "n_items": 2, "values": _CLAIMS[2:], "sources": 1},
        ],
    }


def _write(tmp_path) -> str:
    out = str(tmp_path / "trace.xlsx")
    write_output_excel(_result(), [ColumnSpec(name="Recent news")], out, diag=_diag())
    return out


def test_provenance_claim_ids_sequential_and_unique(tmp_path):
    wb = openpyxl.load_workbook(_write(tmp_path))
    ws = wb["Provenance"]
    header = [c.value for c in ws[1]]
    assert header[0] == "Claim ID"
    ids = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    assert ids == [f"C{i:04d}" for i in range(1, len(_CLAIMS) + 1)]
    wb.close()
    print("OK test_provenance_claim_ids_sequential_and_unique passed")


def test_grouped_themes_bullets_carry_claim_ids(tmp_path):
    wb = openpyxl.load_workbook(_write(tmp_path))
    ws = wb["Grouped Themes"]
    values_cell = str(ws.cell(row=2, column=5).value)
    assert "[C0001]" in values_cell and "[C0002]" in values_cell
    ids_cell = str(ws.cell(row=2, column=6).value)
    assert ids_cell == "C0001, C0002"
    wb.close()
    print("OK test_grouped_themes_bullets_carry_claim_ids passed")


def test_theme_cell_hyperlinks_to_its_provenance_row(tmp_path):
    wb = openpyxl.load_workbook(_write(tmp_path))
    ws = wb["Grouped Themes"]
    link = ws.cell(row=2, column=3).hyperlink  # theme = _CLAIMS[0] = Provenance row 2
    assert link is not None and link.target == "#Provenance!A2"
    link2 = ws.cell(row=3, column=3).hyperlink  # theme = _CLAIMS[2] = Provenance row 4
    assert link2 is not None and link2.target == "#Provenance!A4"
    wb.close()
    print("OK test_theme_cell_hyperlinks_to_its_provenance_row passed")


def test_digest_sheet_template_text_and_link(tmp_path):
    wb = openpyxl.load_workbook(_write(tmp_path))
    assert "Digest" in wb.sheetnames
    # Deliverable-facing order: Summary, Matrix, Digest, Provenance, Grouped Themes.
    names = wb.sheetnames
    assert names.index("Digest") == names.index("Matrix") + 1
    ws = wb["Digest"]
    assert [c.value for c in ws[1]] == ["Entity", "Question", "Items", "Themes", "Digest"]
    digest = str(ws.cell(row=2, column=5).value)
    assert "4 items across 2 themes" in digest
    assert _CLAIMS[0] in digest and "[C0001]" in digest, "top theme label + claim ID must be cited"
    link = ws.cell(row=2, column=2).hyperlink
    assert link is not None and link.target == "#'Grouped Themes'!A2"
    wb.close()
    print("OK test_digest_sheet_template_text_and_link passed")


def test_provenance_source_urls_are_hyperlinks(tmp_path):
    wb = openpyxl.load_workbook(_write(tmp_path))
    ws = wb["Provenance"]
    c = ws.cell(row=2, column=3)
    assert c.hyperlink is not None and c.hyperlink.target == _URL
    wb.close()
    print("OK test_provenance_source_urls_are_hyperlinks passed")


def test_no_digest_or_links_without_groups(tmp_path):
    out = str(tmp_path / "plain.xlsx")
    write_output_excel(_result(), [ColumnSpec(name="Recent news")], out, diag={})
    wb = openpyxl.load_workbook(out)
    assert "Digest" not in wb.sheetnames and "Grouped Themes" not in wb.sheetnames
    # Claim IDs + source-URL links still present — Provenance traceability
    # doesn't depend on grouping.
    ws = wb["Provenance"]
    assert ws.cell(row=2, column=1).value == "C0001"
    assert ws.cell(row=2, column=3).hyperlink is not None
    wb.close()
    print("OK test_no_digest_or_links_without_groups passed")
