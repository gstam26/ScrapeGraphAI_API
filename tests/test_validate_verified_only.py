"""
Tests for diagnostics/validate_verified_only.py — the mechanical
verified-only workbook checker. Builds real workbooks via write_output_excel;
the violating case injects hand-built claim_groups citing an unverified claim
(the sheet renderers don't enforce the policy — group.py does — so the
checker must catch a workbook produced with stale/bypassed grouping).
"""
from diagnostics.validate_verified_only import check_workbook
from models import ColumnSpec, ExtractedCell, ExtractedRow, PipelineResult, SourceQuote
from src.io_excel import write_output_excel

_URL = "https://example.com/news"


def _result(verified_flags: list[bool]) -> PipelineResult:
    claims = [f"claim number {i}" for i in range(len(verified_flags))]
    evidence = [
        SourceQuote(value=c, quote=c if v else "mangled", source_url=_URL, verified=v)
        for c, v in zip(claims, verified_flags)
    ]
    cell = ExtractedCell(entity="TestCo", source_url=_URL, column="Recent news",
                         value=claims, evidence=evidence, verified=all(verified_flags))
    return PipelineResult(rows=[ExtractedRow(entity="TestCo", cells=[cell], all_cells=[cell])])


def _groups(values: list[str]) -> dict:
    return {"claim_groups": [
        {"entity": "TestCo", "question": "Recent news", "theme": values[0],
         "n_items": len(values), "values": values, "sources": 1},
    ]}


def test_compliant_workbook_passes(tmp_path):
    out = str(tmp_path / "clean.xlsx")
    # 2 verified claims grouped; 1 unverified claim present in Provenance only.
    result = _result([True, True, False])
    write_output_excel(result, [ColumnSpec(name="Recent news")], out,
                       diag=_groups(["claim number 0", "claim number 1"]))
    assert check_workbook(out) == []
    print("OK test_compliant_workbook_passes passed")


def test_unverified_citation_and_anchor_flagged(tmp_path):
    out = str(tmp_path / "dirty.xlsx")
    # Groups cite claim 2, which is unverified — and it's also the theme/anchor.
    result = _result([True, True, False])
    write_output_excel(result, [ColumnSpec(name="Recent news")], out,
                       diag=_groups(["claim number 2", "claim number 0"]))
    violations = check_workbook(out)
    assert any("UNVERIFIED claim C0003" in v for v in violations), violations
    assert any("anchors on UNVERIFIED" in v for v in violations), violations
    print("OK test_unverified_citation_and_anchor_flagged passed")


def test_matrix_diff_against_identical_baseline(tmp_path):
    a = str(tmp_path / "a.xlsx")
    b = str(tmp_path / "b.xlsx")
    result = _result([True, True])
    groups = _groups(["claim number 0", "claim number 1"])
    write_output_excel(result, [ColumnSpec(name="Recent news")], a, diag=groups)
    write_output_excel(result, [ColumnSpec(name="Recent news")], b, diag=groups)
    assert check_workbook(a, baseline_path=b) == []
    print("OK test_matrix_diff_against_identical_baseline passed")
