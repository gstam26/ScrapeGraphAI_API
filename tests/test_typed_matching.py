"""
Typed (numeric) value matching in generic_eval — regression tests for the
2026-07-21 label finding: token_sort_ratio("2003", "2004") = 75% confidently
auto-matched two DIFFERENT years. Pure-numeric pairs now compare exactly.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.eval.generic_eval import (
    AIRow,
    GTRow,
    _norm,
    _numeric_value,
    evaluate,
)


def _gt(value, question="Year founded"):
    return GTRow(entity="Acme", entity_norm=_norm("Acme"), question=question,
                 question_norm=_norm(question), value=value, is_list=False,
                 verbatim_quote="", source_url="", notes="", is_null=False)


def _ai(value, question="Year founded"):
    return AIRow(entity="Acme", entity_norm=_norm("Acme"), question=question,
                 question_norm=_norm(question), value=value, quote="",
                 verified=True, match_type="exact", source_url="")


def test_numeric_value_parsing():
    assert _numeric_value("2003") == 2003.0
    assert _numeric_value("2,003") == 2003.0
    assert _numeric_value(" 12.5 ") == 12.5
    assert _numeric_value("2003 employees") is None
    assert _numeric_value("about") is None
    assert _numeric_value("") is None


def test_different_years_no_longer_match():
    # The bug George's labels caught: "2003" vs "2004" was an auto_match.
    r = evaluate([_gt("2003")], [_ai("2004")], semantic=False)
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert verdicts == ["auto_miss"]
    assert r.overall["TP"] == 0 and r.overall["FN"] == 1 and r.overall["FP"] == 1


def test_equal_numbers_match_across_formatting():
    r = evaluate([_gt("2,003")], [_ai("2003")], semantic=False)
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert verdicts == ["auto_match"]


def test_identical_years_still_auto_match():
    r = evaluate([_gt("2003")], [_ai("2003")], semantic=False)
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert verdicts == ["auto_match"]


def test_non_numeric_values_keep_fuzzy_matching():
    # Mixed text values are untouched by the typed path.
    r = evaluate([_gt("Geneva, Switzerland", "HQ")],
                 [_ai("Geneva Switzerland", "HQ")], semantic=False)
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert verdicts == ["auto_match"]
