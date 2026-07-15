"""
Tests for diagnostics/generic_eval.py — the domain-agnostic evaluator.

Covers the three properties that were broken or added on 2026-07-15:
  * Provenance column drift ('Question'/'Verbatim Quote' vs old 'Column'/'Quote').
  * Semantic value matching rescues a correct paraphrase that lexical scoring
    double-penalised as miss + hallucination (the Wikimedia mission case).
  * Redundant restatements of a credited claim are dropped from the precision
    denominator, not counted as hallucinations.

Embeddings are injected (no Ollama needed): tests monkeypatch embed_values
with hand-built vectors, exactly the way the grouping tests fake nomic-embed.
"""
import os
import tempfile

import pandas as pd

import diagnostics.generic_eval as ge
from diagnostics.generic_eval import (
    AIRow, GTRow, _dedup_ai, _norm, evaluate, read_pipeline_output,
)


def _gt(entity, question, value, is_list=False, quote=""):
    return GTRow(entity, _norm(entity), question, _norm(question),
                 value, is_list, quote, "", "", "not disclosed" in value.lower())


def _ai(entity, question, value, quote="", mt="exact", verified=True):
    return AIRow(entity, _norm(entity), question, _norm(question),
                 value, quote, verified, mt, "")


def _inject_embeddings(monkeypatch, keyword_axes):
    """Fake embed_values: each text maps to a unit axis chosen by the first
    matching keyword, so same-topic texts get cosine 1 and others 0."""
    def fake(texts):
        dim = len(keyword_axes)
        out = {}
        for t in set(texts):
            vec = [0.0] * dim
            for i, kws in enumerate(keyword_axes):
                if any(k in t.lower() for k in kws):
                    vec[i] = 1.0
                    break
            out[t] = vec
        return out
    monkeypatch.setattr(ge, "embed_values", fake)


# ── Provenance schema drift ──────────────────────────────────────────────────

def test_reads_current_provenance_schema():
    # Current io_excel writes 'Question' and 'Verbatim Quote'; the evaluator
    # must not raise 'missing required columns' on them.
    prov = pd.DataFrame([{
        "Claim ID": "C0001", "Entity": "ISO", "Source URL": "http://x",
        "Question": "Headquarters location", "Claim": "Geneva, Switzerland",
        "Verbatim Quote": "headquartered in Geneva", "Verified": True,
        "Match Type": "exact",
    }])
    tmp = os.path.join(tempfile.gettempdir(), "prov_schema_test.xlsx")
    with pd.ExcelWriter(tmp) as w:
        pd.DataFrame({"Entity": ["ISO"]}).to_excel(w, sheet_name="Matrix", index=False)
        prov.to_excel(w, sheet_name="Provenance", index=False)
    rows = read_pipeline_output(tmp)
    assert len(rows) == 1
    assert rows[0].question == "Headquarters location"
    assert rows[0].quote == "headquartered in Geneva"
    print("OK test_reads_current_provenance_schema passed")


# ── Semantic rescue ──────────────────────────────────────────────────────────

# Lexically DISJOINT phrasings of the same idea (no shared content tokens), so
# only meaning — not word overlap — can match them.
_GT_MISSION = "Provide free educational resources to everyone"
_AI_MISSION = "Empower global communities through open knowledge sharing"
_MISSION_AXIS = [["educational", "resources", "free", "empower", "knowledge", "communities"]]


def test_lexical_only_double_penalises_paraphrase():
    gt = [_gt("Wikimedia", "Primary mission", _GT_MISSION)]
    ai = [_ai("Wikimedia", "Primary mission", _AI_MISSION)]
    r = evaluate(gt, ai, semantic=False)
    # The correct paraphrase is scored as BOTH a miss and a hallucination.
    assert r.overall["TP"] == 0 and r.overall["FN"] == 1 and r.overall["FP"] == 1
    print("OK test_lexical_only_double_penalises_paraphrase passed")


def test_semantic_rescue_promotes_paraphrase_to_tp(monkeypatch):
    _inject_embeddings(monkeypatch, _MISSION_AXIS)
    gt = [_gt("Wikimedia", "Primary mission", _GT_MISSION)]
    ai = [_ai("Wikimedia", "Primary mission", _AI_MISSION)]
    r = evaluate(gt, ai, semantic=True)
    assert r.overall["TP"] == 1 and r.overall["FN"] == 0 and r.overall["FP"] == 0
    assert r.overall["semantic_rescues"] == 1
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert "semantic_review" in verdicts
    print("OK test_semantic_rescue_promotes_paraphrase_to_tp passed")


def test_semantic_never_upgrades_to_confident_auto_match(monkeypatch):
    # A semantic-only match caps at the flagged 'semantic_review' band, never a
    # silent 'auto_match' on an unvalidated cosine.
    _inject_embeddings(monkeypatch, [["knowledge", "empower"]])
    gt = [_gt("W", "Mission", "universal access to knowledge")]
    ai = [_ai("W", "Mission", "empower people to share freely")]
    r = evaluate(gt, ai, semantic=True)
    p = r.cells[0].gt_pairs[0]
    assert p.verdict == "semantic_review"
    print("OK test_semantic_never_upgrades_to_confident_auto_match passed")


# ── Redundant restatements ───────────────────────────────────────────────────

def test_redundant_restatements_not_counted_as_hallucination(monkeypatch):
    _inject_embeddings(monkeypatch, [["geneva"], ["standard"]])
    gt = [_gt("ISO", "Headquarters location", "Geneva, Switzerland")]
    ai = [_ai("ISO", "Headquarters location", "Geneva, Switzerland"),
          _ai("ISO", "Headquarters location", "Geneva"),
          _ai("ISO", "Headquarters location", "based in Geneva, Switzerland")]
    r = evaluate(gt, ai, semantic=True)
    assert r.overall["TP"] == 1 and r.overall["FP"] == 0
    assert r.overall["redundant_dropped"] == 2
    print("OK test_redundant_restatements_not_counted_as_hallucination passed")


def test_genuine_extra_claim_still_counts_as_fp(monkeypatch):
    # A leftover AI claim that is NOT a restatement of a credited claim stays a
    # hallucination — the redundancy filter must not swallow real extras.
    _inject_embeddings(monkeypatch, [["geneva"], ["london"]])
    gt = [_gt("ISO", "Headquarters location", "Geneva, Switzerland")]
    ai = [_ai("ISO", "Headquarters location", "Geneva, Switzerland"),
          _ai("ISO", "Headquarters location", "London, United Kingdom")]
    r = evaluate(gt, ai, semantic=True)
    assert r.overall["TP"] == 1 and r.overall["FP"] == 1
    assert r.overall["redundant_dropped"] == 0
    print("OK test_genuine_extra_claim_still_counts_as_fp passed")


# ── Fuzzy dedup ──────────────────────────────────────────────────────────────

def test_fuzzy_dedup_collapses_reorderings():
    rows = [_ai("E", "Q", "Boston Scientific Corporation"),
            _ai("E", "Q", "Corporation Boston Scientific"),
            _ai("E", "Q", "Stryker")]
    out = _dedup_ai(rows)
    values = sorted(a.value for a in out)
    assert len(out) == 2 and "Stryker" in values
    print("OK test_fuzzy_dedup_collapses_reorderings passed")


if __name__ == "__main__":
    test_reads_current_provenance_schema()
    test_lexical_only_double_penalises_paraphrase()
    test_fuzzy_dedup_collapses_reorderings()
    print("run via pytest for the monkeypatch-based tests")
