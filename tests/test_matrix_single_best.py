"""Single-answer display discipline (MATRIX_SINGLE_BEST, flag-gated):
one lead answer per single-answer cell, rest demoted to Provenance —
conflict flag retained, list columns untouched, eval sees only the lead."""
import openpyxl
import pytest

import src.io_excel as io_excel
from models import ColumnSpec, ExtractedCell, ExtractedRow, PipelineResult, SourceQuote


def _sq(value, verified=True):
    return SourceQuote(value=value, quote=f"quote {value}", verified=verified)


def _result(values, evidence, column="Where is the company headquarters located?"):
    cell = ExtractedCell(
        entity="Acme", source_url="https://acme.com", column=column,
        value=values, evidence=evidence, verified=True,
        has_conflict=len(values) > 1, num_sources=len(evidence),
        num_unique_values=len(values),
    )
    return PipelineResult(rows=[ExtractedRow(entity="Acme", cells=[cell])])


HQ = ColumnSpec(name="Where is the company headquarters located?",
                instruction="City and country.")
COUNTRIES = ColumnSpec(
    name="In which country/countries does manufacturing take place?",
    instruction="List all countries, one per line.")


def test_flag_off_keeps_all_candidates(monkeypatch):
    monkeypatch.setattr(io_excel, "MATRIX_SINGLE_BEST", False)
    result = _result(["Hong Kong", "Dongguan, China"],
                     [_sq("Hong Kong"), _sq("Dongguan, China")])
    df, _ = io_excel._make_matrix_df(result, [HQ])
    text = df.iloc[0][HQ.name]
    assert "Hong Kong" in text and "Dongguan, China" in text
    assert "other candidate" not in text


def test_flag_on_leads_with_most_attested(monkeypatch):
    monkeypatch.setattr(io_excel, "MATRIX_SINGLE_BEST", True)
    # Hong Kong attested by 3 evidence items, Dongguan by 1.
    result = _result(
        ["Hong Kong", "Dongguan, China"],
        [_sq("Hong Kong"), _sq("Hong Kong"), _sq("Hong Kong"),
         _sq("Dongguan, China")])
    df, fills = io_excel._make_matrix_df(result, [HQ])
    text = df.iloc[0][HQ.name]
    assert "- Hong Kong" in text
    assert "Dongguan" not in text
    assert "[+1 other candidate(s) — see Provenance]" in text
    assert "(sources conflict)" in text          # honesty flag survives
    assert (2, 2) in fills                        # orange fill survives


def test_flag_on_verified_beats_attestation(monkeypatch):
    monkeypatch.setattr(io_excel, "MATRIX_SINGLE_BEST", True)
    # Unverified value has more attestations; verified must still lead.
    result = _result(
        ["Osaka, Japan", "Nuneaton, UK"],
        [_sq("Osaka, Japan", verified=True),
         _sq("Nuneaton, UK", verified=False), _sq("Nuneaton, UK", verified=False)])
    df, _ = io_excel._make_matrix_df(result, [HQ])
    text = df.iloc[0][HQ.name]
    assert "- Osaka, Japan" in text and "Nuneaton" not in text


def test_flag_on_list_columns_untouched(monkeypatch):
    monkeypatch.setattr(io_excel, "MATRIX_SINGLE_BEST", True)
    result = _result(["China", "Vietnam", "Thailand"],
                     [_sq("China"), _sq("Vietnam"), _sq("Thailand")],
                     column=COUNTRIES.name)
    df, _ = io_excel._make_matrix_df(result, [COUNTRIES])
    text = df.iloc[0][COUNTRIES.name]
    for c in ("China", "Vietnam", "Thailand"):
        assert c in text
    assert "other candidate" not in text


def test_eval_scores_only_the_lead(monkeypatch, tmp_path):
    """Round-trip: the demoted-candidate marker must be invisible to the
    matrix reader, so a demoted candidate is no longer a scored claim."""
    monkeypatch.setattr(io_excel, "MATRIX_SINGLE_BEST", True)
    result = _result(["Hong Kong", "Dongguan, China"],
                     [_sq("Hong Kong"), _sq("Hong Kong"), _sq("Dongguan, China")])
    df, _ = io_excel._make_matrix_df(result, [HQ])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Matrix"
    ws.append(list(df.columns))
    for _, row in df.iterrows():
        ws.append(list(row))
    path = tmp_path / "out.xlsx"
    wb.save(path)

    from src.eval.generic_eval import read_pipeline_matrix
    values = [r.value for r in read_pipeline_matrix(str(path))]
    assert values == ["Hong Kong"]


def test_flag_on_prose_cells_exempt(monkeypatch):
    """A multi-sentence description is one composed answer, not competing
    candidates — demoting its sentences would fight the prose-cell join
    (measured: v1 preview lost 27 TPs, prose exemption recovered 14)."""
    monkeypatch.setattr(io_excel, "MATRIX_SINGLE_BEST", True)
    long_a = "Acme designs and manufactures precision medical devices for global healthcare clients."
    long_b = "It also provides end-to-end contract manufacturing services from design to logistics."
    result = _result([long_a, long_b], [_sq(long_a), _sq(long_b)],
                     column="Summary description of company's services")
    col = ColumnSpec(name="Summary description of company's services",
                     instruction="1-3 sentences.")
    df, _ = io_excel._make_matrix_df(result, [col])
    text = df.iloc[0][col.name]
    assert long_a in text and long_b in text
    assert "other candidate" not in text
