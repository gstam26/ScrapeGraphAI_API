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


# ── Typed binary matching (2026-07-29: CE credited No↔Yes at 0.97) ───────────

class _YesEqualsNoScorer:
    """Adversarial semantic scorer reproducing the relevance-CE trap: bare
    binary values are topically identical, so it scores every pair 0.97."""
    name = "adversarial"
    min_score = 0.5
    decisive = True
    def score(self, a, b): return 0.97


def _mk(gt_value, ai_value):
    from src.eval.generic_eval import GTRow, AIRow
    gt = GTRow(entity="E", entity_norm="e", question="Q?", question_norm="q",
               value=gt_value, is_list=False, verbatim_quote="",
               source_url="", notes="", is_null=False)
    ai = AIRow(entity="E", entity_norm="e", question="Q?", question_norm="q",
               value=ai_value, quote="", verified=True, match_type="exact",
               source_url="")
    return gt, ai


def test_binary_polarity_never_semantic():
    from src.eval.generic_eval import _pair_score
    gt, ai = _mk("Yes", "No")
    value_score, _, combined, semantic = _pair_score(gt, ai, sem=_YesEqualsNoScorer())
    assert value_score == 0.0 and combined == 0.0 and semantic == 0.0


def test_binary_same_polarity_cross_form_matches():
    from src.eval.generic_eval import _pair_score
    for a, b in (("Yes", "yes."), ("No", "false"), ("True", "Yes")):
        gt, ai = _mk(a, b)
        value_score, _, combined, _ = _pair_score(gt, ai, sem=None)
        assert value_score == 1.0 and combined == 1.0, (a, b)


def test_binary_rule_ignores_prose_values():
    """'Yes, in the UK' is not a bare binary — normal path applies."""
    from src.eval.generic_eval import _pair_score
    gt, ai = _mk("Yes, in the UK", "No")
    _, _, _, semantic = _pair_score(gt, ai, sem=_YesEqualsNoScorer())
    assert semantic == 0.97  # semantic path still consulted for prose
