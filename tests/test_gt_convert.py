"""
Tests for src/eval/gt_convert.py — the analyst-matrix -> flat GT converter.

All offline: matrices are built as DataFrames (and one real .xlsx round-trip
through generic_eval.read_gt, the converter's own acceptance bar).
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
import pytest

from src.eval.gt_convert import (
    _CANONICAL_NULL,
    convert,
    read_matrix,
    split_cell,
    write_gt,
)
from src.eval.generic_eval import read_gt


# ---------------------------------------------------------------------------
# split_cell
# ---------------------------------------------------------------------------
def test_split_on_newlines_and_semicolons():
    assert split_cell("Eden Prairie, MN\nBallinasloe, Ireland") == [
        "Eden Prairie, MN", "Ballinasloe, Ireland",
    ]
    assert split_cell("Wikipedia; Wikidata; Commons") == [
        "Wikipedia", "Wikidata", "Commons",
    ]


def test_comma_kept_inside_items_unless_opted_in():
    assert split_cell("Eden Prairie, MN") == ["Eden Prairie, MN"]
    assert split_cell("Firefox, Thunderbird", comma=True) == ["Firefox", "Thunderbird"]


def test_bullets_and_numbering_stripped():
    assert split_cell("- Firefox\n* Thunderbird\n1. MDN\n2) Rust") == [
        "Firefox", "Thunderbird", "MDN", "Rust",
    ]


def test_empty_and_whitespace_cells_split_to_nothing():
    assert split_cell("") == []
    assert split_cell("  \n ; ") == []


# ---------------------------------------------------------------------------
# convert — core behaviour
# ---------------------------------------------------------------------------
def _matrix(rows, columns):
    return pd.DataFrame(rows, columns=columns)


def test_single_answer_column_inferred():
    df = _matrix(
        [["Acme", "2003"], ["Beta", "1996"]],
        ["Company", "Year founded"],
    )
    rows, _ = convert(df)
    assert len(rows) == 2
    assert all(r["is_list"] is False for r in rows)
    assert rows[0] == {
        "entity": "Acme", "question": "Year founded", "value": "2003",
        "is_list": False, "verbatim_quote": "", "source_url": "", "notes": "",
    }


def test_list_column_inferred_from_any_multi_item_cell():
    df = _matrix(
        [["Acme", "Wikipedia\nWikidata"], ["Beta", "Firefox"]],
        ["Company", "Projects"],
    )
    rows, decisions = convert(df)
    # Beta's single item still gets is_list=True — the QUESTION is a list.
    assert all(r["is_list"] is True for r in rows)
    assert any("is_list=True" in d and "Acme" in d for d in decisions)


def test_null_markers_become_canonical_sentinel():
    df = _matrix(
        [["Acme", "none"], ["Beta", "N/A"], ["Gamma", "not disclosed"]],
        ["Company", "R&D location"],
    )
    rows, _ = convert(df)
    assert [r["value"] for r in rows] == [_CANONICAL_NULL] * 3


def test_null_sentinel_is_single_answer_even_in_list_column():
    df = _matrix(
        [["Acme", "FDA clearance\nSeries B"], ["Beta", "none"]],
        ["Company", "Recent news"],
    )
    rows, _ = convert(df)
    by_entity = {r["entity"]: r for r in rows if r["entity"] == "Beta"}
    assert by_entity["Beta"]["value"] == _CANONICAL_NULL
    assert by_entity["Beta"]["is_list"] is False


def test_empty_cell_produces_no_rows():
    df = _matrix(
        [["Acme", "2003"], ["Beta", ""]],
        ["Company", "Year founded"],
    )
    rows, _ = convert(df)
    assert {r["entity"] for r in rows} == {"Acme"}


def test_blank_entity_row_continues_previous_entity():
    # Analysts leave the entity cell blank on continuation rows.
    df = _matrix(
        [["Acme", "Eden Prairie, MN"], ["", "Ballinasloe, Ireland"]],
        ["Company", "R&D location"],
    )
    rows, _ = convert(df)
    assert [(r["entity"], r["value"]) for r in rows] == [
        ("Acme", "Eden Prairie, MN"), ("Acme", "Ballinasloe, Ireland"),
    ]
    assert all(r["is_list"] for r in rows)  # 2 items in the (merged) cell


def test_comma_split_opt_in_per_column():
    df = _matrix(
        [["Acme", "Firefox, Thunderbird", "Eden Prairie, MN"]],
        ["Company", "Projects", "R&D location"],
    )
    rows, _ = convert(df, comma_cols={"Projects"})
    projects = [r["value"] for r in rows if r["question"] == "Projects"]
    location = [r["value"] for r in rows if r["question"] == "R&D location"]
    assert projects == ["Firefox", "Thunderbird"]
    assert location == ["Eden Prairie, MN"]  # comma NOT split here


def test_force_single_never_splits():
    df = _matrix(
        [["Acme", "Open by design\nFueled by imagination"]],
        ["Company", "Primary mission"],
    )
    rows, _ = convert(df, force_single={"Primary mission"})
    assert len(rows) == 1
    assert rows[0]["is_list"] is False
    assert "Open by design" in rows[0]["value"]
    assert "Fueled by imagination" in rows[0]["value"]


def test_force_list_overrides_inference():
    df = _matrix([["Acme", "Firefox"]], ["Company", "Projects"])
    rows, _ = convert(df, force_list={"Projects"})
    assert rows[0]["is_list"] is True


def test_unknown_flag_name_is_hard_error():
    df = _matrix([["Acme", "2003"]], ["Company", "Year founded"])
    with pytest.raises(ValueError, match="not found as question columns"):
        convert(df, comma_cols={"Yaer founded"})  # typo must not pass silently


def test_conflicting_list_single_flags_error():
    df = _matrix([["Acme", "2003"]], ["Company", "Year founded"])
    with pytest.raises(ValueError, match="both --list and --single"):
        convert(df, force_list={"Year founded"}, force_single={"Year founded"})


def test_integer_cells_render_without_decimal_point():
    df = _matrix([["Acme", 2003.0]], ["Company", "Year founded"])
    rows, _ = convert(df)
    assert rows[0]["value"] == "2003"


# ---------------------------------------------------------------------------
# Round-trip: written workbook must parse with the evaluator's own reader
# ---------------------------------------------------------------------------
def test_round_trip_through_generic_eval_reader(tmp_path):
    matrix_path = tmp_path / "analyst_matrix.xlsx"
    gt_path = tmp_path / "ground_truth.xlsx"

    pd.DataFrame(
        [
            ["Acme Dx", "Eden Prairie, MN\nBallinasloe, Ireland", "own-product",
             "- FDA clearance March 2026\n- Series B round"],
            ["Beta Labs", "none", "OEM", ""],
        ],
        columns=["Company", "R&D location", "Company type", "Recent news"],
    ).to_excel(matrix_path, index=False)

    df = read_matrix(str(matrix_path))
    rows, _ = convert(df)
    write_gt(rows, str(gt_path), str(matrix_path))

    parsed = read_gt(str(gt_path))
    # 7 rows: Acme R&D x2 + type + news x2; Beta null-R&D + type ("OEM")
    assert len(parsed) == len(rows) == 7
    by_cell = {}
    for g in parsed:
        by_cell.setdefault((g.entity, g.question), []).append(g)

    rd = by_cell[("Acme Dx", "R&D location")]
    assert {g.value for g in rd} == {"Eden Prairie, MN", "Ballinasloe, Ireland"}
    assert all(g.is_list for g in rd)

    news = by_cell[("Acme Dx", "Recent news")]
    assert {g.value for g in news} == {"FDA clearance March 2026", "Series B round"}

    beta_null = by_cell[("Beta Labs", "R&D location")][0]
    assert beta_null.is_null is True  # generic_eval recognises the sentinel

    assert ("Beta Labs", "Recent news") not in by_cell  # empty cell: no claim
