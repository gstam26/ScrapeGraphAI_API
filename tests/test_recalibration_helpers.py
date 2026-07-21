"""
Tests for the pure helpers behind filter_recalibration's cross-encoder leg:
natural-question extraction, the rank-based AUC (must equal the pairwise
one), and the paired-bootstrap delta CI.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
import pytest

from diagnostics.filter_recalibration import (
    _auc,
    _auc_ranked,
    _natural_question,
    auc_delta_ci,
)


def test_natural_question_extracts_interrogative_sentence():
    instr = ("In which country or countries does the company conduct its R&D? "
             "List each location separately; include city or region if stated.")
    assert _natural_question("R&D location", instr) == (
        "In which country or countries does the company conduct its R&D?"
    )


def test_natural_question_falls_back_to_name():
    instr = "List each distinct diagnostic area, technology, or assay type separately."
    assert _natural_question("Diagnostics type", instr) == "Diagnostics type"


def test_ranked_auc_matches_pairwise_auc_including_ties():
    scores = pd.Series([0.9, 0.7, 0.7, 0.4, 0.2, 0.7])
    answered = pd.Series([True, True, False, False, False, True])
    pos = scores[answered].tolist()
    neg = scores[~answered].tolist()
    assert _auc_ranked(scores, answered) == pytest.approx(_auc(pos, neg))


def test_ranked_auc_degenerate_returns_none():
    assert _auc_ranked(pd.Series([0.5, 0.6]), pd.Series([True, True])) is None


def test_delta_ci_flags_clear_winner_and_noise():
    # Question "clear": scorer B separates perfectly, A is anti-predictive ->
    # large positive delta, CI must exclude 0. Question "noise": identical
    # scorers -> delta 0, CI must straddle/include 0, not significant.
    rows = []
    for i in range(40):
        answered = i < 20
        rows.append({"question": "clear", "url": f"u{i}",
                     "new_score": 1.0 - i * 0.01,   # high on NEGATIVES too
                     "ce": 1.0 if answered else 0.0,
                     "answered": answered})
    # Make A anti-predictive on "clear": invert so positives score low.
    for r in rows:
        r["new_score"] = 0.0 + (0.5 if not r["answered"] else 0.1)
    for i in range(40):
        answered = i < 20
        s = 0.9 - i * 0.001
        rows.append({"question": "noise", "url": f"v{i}",
                     "new_score": s, "ce": s, "answered": answered})
    df = pd.DataFrame(rows)

    ci = auc_delta_ci(df, "new_score", "ce", n_boot=200, seed=1)
    by_q = {r["Question"]: r for _, r in ci.iterrows()}

    clear = by_q["clear"]
    assert clear["Delta (B-A)"] > 0.5
    assert clear["Significant"]

    noise = by_q["noise"]
    assert noise["Delta (B-A)"] == pytest.approx(0.0)
    assert not noise["Significant"]


def test_delta_ci_is_deterministic_for_fixed_seed():
    rows = [{"question": "q", "url": f"u{i}",
             "new_score": (i % 7) / 7, "ce": (i % 5) / 5,
             "answered": i % 3 == 0} for i in range(30)]
    df = pd.DataFrame(rows)
    a = auc_delta_ci(df, "new_score", "ce", n_boot=100, seed=7)
    b = auc_delta_ci(df, "new_score", "ce", n_boot=100, seed=7)
    pd.testing.assert_frame_equal(a, b)
