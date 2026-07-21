"""
Tests for generic_eval's Matrix-scoring mode (read_pipeline_matrix).

The Matrix cell grammar under test is io_excel._make_matrix_df's:
bulleted values, "-- Unverified --" section switch, "(unverified)" whole-cell
marker, "(sources conflict)" prefix, "[+N more...]" / "[truncated ...]"
overflow markers, and "No data found" cells.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from src.eval.generic_eval import (
    GTRow,
    _norm,
    evaluate,
    read_pipeline_matrix,
)


def _write_matrix(tmp_path, rows, columns):
    path = tmp_path / "output.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame(rows, columns=columns).to_excel(
            w, sheet_name="Matrix", index=False
        )
    return str(path)


def _gt(entity, question, value, is_list=False):
    return GTRow(
        entity=entity, entity_norm=_norm(entity),
        question=question, question_norm=_norm(question),
        value=value, is_list=is_list, verbatim_quote="",
        source_url="", notes="", is_null=False,
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_plain_verified_bullets(tmp_path):
    path = _write_matrix(
        tmp_path,
        [["Acme", "- Eden Prairie, MN\n- Ballinasloe, Ireland"]],
        ["Entity", "R&D location"],
    )
    rows = read_pipeline_matrix(path)
    assert [(r.value, r.verified) for r in rows] == [
        ("Eden Prairie, MN", True), ("Ballinasloe, Ireland", True),
    ]
    assert rows[0].entity == "Acme"
    assert rows[0].question == "R&D location"


def test_unverified_section_switch(tmp_path):
    path = _write_matrix(
        tmp_path,
        [["Acme", "- verified claim\n-- Unverified --\n- shaky claim"]],
        ["Entity", "Recent news"],
    )
    rows = read_pipeline_matrix(path)
    assert [(r.value, r.verified) for r in rows] == [
        ("verified claim", True), ("shaky claim", False),
    ]


def test_whole_cell_unverified_marker(tmp_path):
    path = _write_matrix(
        tmp_path,
        [["Acme", "- only claim\n(unverified)"]],
        ["Entity", "Company type"],
    )
    rows = read_pipeline_matrix(path)
    assert [(r.value, r.verified) for r in rows] == [("only claim", False)]


def test_conflict_and_overflow_markers_skipped(tmp_path):
    cell = (
        "(sources conflict)\n- claim A\n- claim B\n"
        "[+12 more items — see Provenance]\n[truncated — full list in Provenance]"
    )
    path = _write_matrix(tmp_path, [["Acme", cell]], ["Entity", "Recent news"])
    rows = read_pipeline_matrix(path)
    assert [r.value for r in rows] == ["claim A", "claim B"]


def test_no_data_found_becomes_null_claim(tmp_path):
    path = _write_matrix(
        tmp_path, [["Acme", "No data found"]], ["Entity", "R&D location"]
    )
    rows = read_pipeline_matrix(path)
    assert len(rows) == 1
    assert rows[0].value == "None (not disclosed)"


def test_missing_matrix_sheet_raises(tmp_path):
    path = tmp_path / "no_matrix.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame([["x"]], columns=["a"]).to_excel(
            w, sheet_name="Provenance", index=False
        )
    try:
        read_pipeline_matrix(str(path))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "no Matrix sheet" in str(e)


# ---------------------------------------------------------------------------
# End-to-end scoring against GT
# ---------------------------------------------------------------------------
def test_matrix_mode_end_to_end_recall_and_null_match(tmp_path):
    path = _write_matrix(
        tmp_path,
        [
            ["Acme", "- Eden Prairie, MN", "No data found"],
            ["Beta", "No data found", "- FDA clearance in March"],
        ],
        ["Entity", "R&D location", "Recent news"],
    )
    ai = read_pipeline_matrix(path)
    gt = [
        _gt("Acme", "R&D location", "Eden Prairie, MN"),
        # Beta's GT confirms absence — deliverable shows "No data found": correct.
        GTRow(
            entity="Beta", entity_norm=_norm("Beta"),
            question="R&D location", question_norm=_norm("R&D location"),
            value="None (not disclosed)", is_list=False, verbatim_quote="",
            source_url="", notes="", is_null=True,
        ),
        _gt("Beta", "Recent news", "FDA clearance in March", is_list=True),
    ]
    result = evaluate(gt, ai, semantic=False)
    o = result.overall
    assert o["TP"] == 3
    assert o["FN"] == 0
    assert o["recall"] == 1.0
    verdicts = {
        (c.entity, p.gt_value): p.verdict
        for c in result.cells for p in c.gt_pairs
    }
    assert verdicts[("Beta", "None (not disclosed)")] == "null_match"


def test_matrix_mode_hidden_overflow_items_count_as_missing(tmp_path):
    # The deliverable shows 1 item + an overflow marker; GT expects 2 items.
    # Matrix mode must score the hidden one as a MISS — measuring the shown
    # table, not the underlying extraction, is the mode's purpose.
    cell = "- shown item\n[+1 more items — see Provenance]"
    path = _write_matrix(tmp_path, [["Acme", cell]], ["Entity", "Projects"])
    ai = read_pipeline_matrix(path)
    gt = [
        _gt("Acme", "Projects", "shown item", is_list=True),
        _gt("Acme", "Projects", "hidden item", is_list=True),
    ]
    result = evaluate(gt, ai, semantic=False)
    assert result.overall["TP"] == 1
    assert result.overall["FN"] == 1
