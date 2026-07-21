"""
Tests for the matcher-validation harness (src/eval/matcher_eval.py) and the
experimental cross-encoder scorer (src/eval/cross_encoder.py).

All offline: the cross-encoder tests inject a fake model (the real one needs
sentence-transformers + local model files); harness tests run on in-memory
EvalResults and tmp_path workbooks.
"""
import math
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
import pytest

import src.eval.cross_encoder as ce_mod
import src.eval.generic_eval as ge
from src.eval.cross_encoder import CrossEncoderScorer, _sigmoid
from src.eval.generic_eval import evaluate, _norm, GTRow, AIRow
from src.eval.matcher_eval import (
    AGREEMENT_BAR,
    build_template_rows,
    score_labels,
    write_template,
    _LABEL_COLUMNS,
)


class FakeModel:
    """Stands in for sentence_transformers.CrossEncoder: returns a logit per
    pair — high when the two texts share a first word, low otherwise."""

    def __init__(self):
        self.calls = []

    def predict(self, pairs):
        self.calls.append(list(pairs))
        return [
            4.0 if a.split()[0].lower() == b.split()[0].lower() else -4.0
            for a, b in pairs
        ]


# ---------------------------------------------------------------------------
# CrossEncoderScorer
# ---------------------------------------------------------------------------
def test_scores_are_sigmoid_of_logits():
    scorer = CrossEncoderScorer(model=FakeModel())
    assert scorer.score("geneva hq", "geneva office") == pytest.approx(_sigmoid(4.0))
    assert scorer.score("geneva hq", "london office") == pytest.approx(_sigmoid(-4.0))


def test_pair_cache_prevents_repeat_model_calls():
    model = FakeModel()
    scorer = CrossEncoderScorer(model=model)
    scorer.score("a x", "a y")
    scorer.score("a x", "a y")
    scorer.score_pairs([("a x", "a y"), ("b x", "b y")])
    # 1st call scores the new pair; repeat is served from cache; the batch
    # only sends its one cache miss.
    assert [len(c) for c in model.calls] == [1, 1]


def test_batch_scoring_preserves_order():
    scorer = CrossEncoderScorer(model=FakeModel())
    pairs = [("a x", "b y"), ("c x", "c y"), ("d x", "e y")]
    out = scorer.score_pairs(pairs)
    assert out[1] > 0.5
    assert out[0] < 0.5 and out[2] < 0.5


# ---------------------------------------------------------------------------
# generic_eval --semantic-backend cross-encoder wiring
# ---------------------------------------------------------------------------
def _gt(entity, question, value, is_list=False):
    return GTRow(entity=entity, entity_norm=_norm(entity), question=question,
                 question_norm=_norm(question), value=value, is_list=is_list,
                 verbatim_quote="", source_url="", notes="", is_null=False)


def _ai(entity, question, value):
    return AIRow(entity=entity, entity_norm=_norm(entity), question=question,
                 question_norm=_norm(question), value=value, quote="",
                 verified=True, match_type="exact", source_url="")


def test_cross_encoder_backend_rescues_paraphrase(monkeypatch):
    # Lexically distant pair; the (faked) cross-encoder says SAME because
    # both start with the same token. Must surface as semantic_review —
    # flagged, never a confident auto_match.
    monkeypatch.setattr(
        ce_mod, "CrossEncoderScorer",
        lambda: CrossEncoderScorer(model=FakeModel()),
    )
    gt = [_gt("Acme", "Mission", "knowledge access for every human being")]
    ai = [_ai("Acme", "Mission", "knowledge shared freely worldwide")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="cross-encoder")
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert verdicts == ["semantic_review"]
    assert r.overall["semantic_rescues"] == 1


def test_cross_encoder_unavailable_falls_back_to_lexical(monkeypatch):
    def _boom():
        raise ImportError("sentence_transformers not installed")
    monkeypatch.setattr(ce_mod, "CrossEncoderScorer", _boom)
    gt = [_gt("Acme", "Mission", "knowledge access for every human")]
    ai = [_ai("Acme", "Mission", "knowledge shared freely worldwide")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="cross-encoder")
    # Lexical-only: the pair stays a miss; the run itself must not crash.
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert verdicts == ["auto_miss"]


# ---------------------------------------------------------------------------
# matcher_eval: label-template
# ---------------------------------------------------------------------------
def _small_result():
    gt = [
        _gt("Acme", "Location", "Geneva"),
        _gt("Acme", "Location", "totally unrelated fact"),
    ]
    ai = [
        _ai("Acme", "Location", "Geneva, Switzerland"),
        _ai("Acme", "Location", "sells lab equipment"),
    ]
    return evaluate(gt, ai, semantic=False)


def test_template_rows_cover_matches_and_near_misses():
    rows = build_template_rows(_small_result())
    says = {(r["gt_value"], r["matcher_says"]) for r in rows}
    assert ("Geneva", "SAME") in says
    assert ("totally unrelated fact", "DIFFERENT") in says
    assert all(r["human_label"] == "" for r in rows)


def test_template_workbook_round_trip(tmp_path):
    rows = build_template_rows(_small_result())
    path = tmp_path / "labels.xlsx"
    write_template(rows, str(path))
    df = pd.read_excel(path, sheet_name="Pairs")
    assert list(df.columns) == _LABEL_COLUMNS
    assert len(df) == len(rows)


# ---------------------------------------------------------------------------
# matcher_eval: label-score
# ---------------------------------------------------------------------------
def _labels_df(rows):
    return pd.DataFrame(rows, columns=_LABEL_COLUMNS)


def _label_row(verdict, says, human):
    return {
        "entity": "E", "question": "Q", "is_list": False,
        "gt_value": "g", "ai_value": "a", "lexical": 0.5, "semantic": 0.0,
        "combined": 0.5, "matcher_verdict": verdict, "matcher_says": says,
        "human_label": human, "notes": "",
    }


def test_label_score_agreement_and_bands():
    df = _labels_df([
        _label_row("auto_match", "SAME", "SAME"),
        _label_row("auto_match", "SAME", "SAME"),
        _label_row("review", "SAME", "DIFFERENT"),   # matcher over-credits
        _label_row("auto_miss", "DIFFERENT", "DIFFERENT"),
        _label_row("auto_miss", "DIFFERENT", ""),    # unlabelled -> excluded
    ])
    report = score_labels(df)
    assert report["n_labelled"] == 4
    assert report["agreement"] == pytest.approx(3 / 4)
    assert report["per_band"]["auto_match"]["agreement"] == 1.0
    assert report["per_band"]["review"]["agreement"] == 0.0
    assert report["confusion"]["matcher_same_human_diff"] == 1
    assert report["passed_bar"] is (3 / 4 >= AGREEMENT_BAR)


def test_label_score_requires_labels():
    df = _labels_df([_label_row("auto_match", "SAME", "")])
    with pytest.raises(ValueError, match="No labelled rows"):
        score_labels(df)


# ---------------------------------------------------------------------------
# matcher_eval: ce-rescore
# ---------------------------------------------------------------------------
def test_ce_rescore_compares_matchers_and_sweeps_threshold():
    from src.eval.matcher_eval import ce_rescore

    rows = [
        # Human SAME pairs share a first token (FakeModel scores them high).
        {**_label_row("auto_miss", "DIFFERENT", "SAME"),
         "gt_value": "geneva hq", "ai_value": "geneva office"},
        {**_label_row("auto_match", "SAME", "SAME"),
         "gt_value": "paris lab", "ai_value": "paris facility"},
        # Human DIFFERENT pair with different first tokens (scored low).
        {**_label_row("auto_match", "SAME", "DIFFERENT"),
         "gt_value": "london", "ai_value": "berlin"},
        # Unlabelled row must be excluded.
        {**_label_row("auto_miss", "DIFFERENT", ""),
         "gt_value": "x", "ai_value": "y"},
    ]
    df = _labels_df(rows)
    scorer = CrossEncoderScorer(model=FakeModel())
    report = ce_rescore(df, scorer=scorer)

    assert report["n_labelled"] == 3
    # Production matcher: wrong on rows 1 and 3 -> 1/3.
    assert report["matcher_agreement"] == pytest.approx(1 / 3, abs=1e-3)
    # Fake CE separates perfectly -> 3/3 at its default 0.5 threshold.
    assert report["ce_agreement_at_default"] == 1.0
    assert report["ce_agreement_at_best"] == 1.0
    assert len(report["sweep"]) == 19
    assert len(report["scores"]) == 3
