"""
Tests for src/eval/generic_eval.py — the domain-agnostic evaluator.

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

import src.eval.generic_eval as ge
from src.eval.generic_eval import (
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
    r = evaluate(gt, ai, semantic=True, semantic_backend="ollama")
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
    r = evaluate(gt, ai, semantic=True, semantic_backend="ollama")
    p = r.cells[0].gt_pairs[0]
    assert p.verdict == "semantic_review"
    print("OK test_semantic_never_upgrades_to_confident_auto_match passed")


def test_semantic_disabled_on_list_items(monkeypatch):
    # Distinct named list items (Firefox / Thunderbird / Common Voice) embed to
    # nearly one axis; semantic must NOT credit one project for another. On a
    # list cell a lexically-distinct AI item leaves the GT item a miss.
    _inject_embeddings(monkeypatch, [["firefox", "thunderbird", "common voice", "webmaker"]])
    gt = [_gt("Mozilla", "Main projects", "Firefox", is_list=True),
          _gt("Mozilla", "Main projects", "Thunderbird", is_list=True)]
    ai = [_ai("Mozilla", "Main projects", "Common Voice"),
          _ai("Mozilla", "Main projects", "Webmaker")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="ollama")
    # No semantic rescue: both GT items miss, both AI items are FP (real extras).
    assert r.overall["TP"] == 0 and r.overall["semantic_rescues"] == 0
    print("OK test_semantic_disabled_on_list_items passed")


# ── Redundant restatements ───────────────────────────────────────────────────

def test_redundant_restatements_not_counted_as_hallucination(monkeypatch):
    _inject_embeddings(monkeypatch, [["geneva"], ["standard"]])
    gt = [_gt("ISO", "Headquarters location", "Geneva, Switzerland")]
    ai = [_ai("ISO", "Headquarters location", "Geneva, Switzerland"),
          _ai("ISO", "Headquarters location", "Geneva"),
          _ai("ISO", "Headquarters location", "based in Geneva, Switzerland")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="ollama")
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
    r = evaluate(gt, ai, semantic=True, semantic_backend="ollama")
    assert r.overall["TP"] == 1 and r.overall["FP"] == 1
    assert r.overall["redundant_dropped"] == 0
    print("OK test_genuine_extra_claim_still_counts_as_fp passed")


# ── Decisive cross-encoder backend ───────────────────────────────────────────
# The 2026-07-22 rewire: when the semantic scorer is the cross-encoder it
# DECIDES equivalence (veto + rescue) on list AND single-answer cells, rather
# than only rescuing single-answer prose like the embedding cosine.

class _FakeDecisiveScorer:
    """Stand-in for the cross-encoder: decides equivalence from an explicit
    SAME set, so a test controls exactly which pairs it credits or vetoes."""
    name = "fake cross-encoder (test)"
    min_score = 0.5
    decisive = True

    def __init__(self, same_pairs):
        self._same = {frozenset((_norm(a), _norm(b))) for a, b in same_pairs}

    def score(self, a, b):
        return 1.0 if frozenset((_norm(a), _norm(b))) in self._same else 0.0


def _inject_decisive(monkeypatch, same_pairs):
    scorer = _FakeDecisiveScorer(same_pairs)
    monkeypatch.setattr(ge, "_build_semantic", lambda texts, backend: scorer)


def test_ce_vetoes_lexical_list_false_positive(monkeypatch):
    # The -ology trap: lexical scores 'Urology' vs 'laryngology' as a confident
    # auto_match (~0.67), but they are different specialties. A decisive CE that
    # judges them NOT equivalent must VETO the match, leaving the GT item a miss
    # and the AI item a real hallucination. (The production matcher failed all
    # 9 of these on task2, 2026-07-22.)
    _inject_decisive(monkeypatch, same_pairs=[])  # CE credits nothing
    gt = [_gt("BSC", "Product areas", "Urology", is_list=True)]
    ai = [_ai("BSC", "Product areas", "laryngology")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="cross-encoder")
    assert r.overall["TP"] == 0 and r.overall["FN"] == 1 and r.overall["FP"] == 1
    assert [p.verdict for c in r.cells for p in c.gt_pairs] == ["auto_miss"]
    print("OK test_ce_vetoes_lexical_list_false_positive passed")


def test_ce_credits_list_paraphrase_that_embeddings_forbid(monkeypatch):
    # Mirror image: a lexically-mild list pair the CE judges equivalent is
    # credited — a rescue the embedding path refuses on lists (anisotropy guard).
    _inject_decisive(monkeypatch, same_pairs=[("Knee", "Knees")])
    gt = [_gt("Z", "Product areas", "Knee", is_list=True)]
    ai = [_ai("Z", "Product areas", "Knees")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="cross-encoder")
    assert r.overall["TP"] == 1 and r.overall["FP"] == 0
    print("OK test_ce_credits_list_paraphrase_that_embeddings_forbid passed")


def test_ce_numeric_identity_not_vetoed(monkeypatch):
    # Guard: the typed-numeric path reports semantic=0 for '2003' vs '2003'.
    # A decisive backend must still credit the identity via the exact-match
    # path, not read the 0 as a veto and drop a correct year.
    _inject_decisive(monkeypatch, same_pairs=[])  # CE would veto everything
    gt = [_gt("M", "Year founded", "2003")]
    ai = [_ai("M", "Year founded", "2003")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="cross-encoder")
    assert r.overall["TP"] == 1 and r.overall["FN"] == 0
    assert [p.verdict for c in r.cells for p in c.gt_pairs] == ["auto_match"]
    print("OK test_ce_numeric_identity_not_vetoed passed")


def test_ce_vetoes_single_answer_anisotropy_error(monkeypatch):
    # The two single-answer errors CE fixes on task2: distinct proper nouns the
    # embedding cosine falsely rated 1.0 (Kalamazoo/Portage, Tornos/Rentas).
    _inject_decisive(monkeypatch, same_pairs=[])
    gt = [_gt("Z", "Current CEO", "Ivan Tornos")]
    ai = [_ai("Z", "Current CEO", "Jennifer Rentas")]
    r = evaluate(gt, ai, semantic=True, semantic_backend="cross-encoder")
    assert r.overall["TP"] == 0 and r.overall["FN"] == 1 and r.overall["FP"] == 1
    print("OK test_ce_vetoes_single_answer_anisotropy_error passed")


# ── Fuzzy dedup ──────────────────────────────────────────────────────────────

def test_fuzzy_dedup_collapses_reorderings():
    rows = [_ai("E", "Q", "Boston Scientific Corporation"),
            _ai("E", "Q", "Corporation Boston Scientific"),
            _ai("E", "Q", "Stryker")]
    out = _dedup_ai(rows)
    values = sorted(a.value for a in out)
    assert len(out) == 2 and "Stryker" in values
    print("OK test_fuzzy_dedup_collapses_reorderings passed")


def test_single_and_list_blocks_reported_separately():
    # Single-answer questions are the trustworthy headline; list precision is a
    # separate lower-bound block (George's decision 2026-07-16).
    gt = [_gt("M", "Year founded", "2003"),
          _gt("M", "Projects", "Firefox", is_list=True),
          _gt("M", "Projects", "Thunderbird", is_list=True)]
    ai = [_ai("M", "Year founded", "2003"),
          _ai("M", "Projects", "Firefox"),
          _ai("M", "Projects", "Common Voice")]  # real-but-unlisted extra
    r = evaluate(gt, ai, semantic=False)
    assert r.overall["single"]["precision"] == 1.0 and r.overall["single"]["FP"] == 0
    assert r.overall["list"]["TP"] == 1 and r.overall["list"]["FP"] == 1
    assert r.per_question["Projects"]["is_list"] is True
    assert r.per_question["Year founded"]["is_list"] is False
    print("OK test_single_and_list_blocks_reported_separately passed")


if __name__ == "__main__":
    test_reads_current_provenance_schema()
    test_lexical_only_double_penalises_paraphrase()
    test_fuzzy_dedup_collapses_reorderings()
    print("run via pytest for the monkeypatch-based tests")


# ── Page-local "Not disclosed" suppression (pre-registered 2026-07-22) ───────
# The extractor emits per-page absence claims beside substantive answers in
# the same cell ("Yes [C0002]; Not disclosed [C0079]"). Page-local absence is
# not a competing answer: suppressed from precision when a substantive claim
# exists, but kept (null_match-able) when the cell has ONLY null claims.

def test_page_local_null_suppressed_beside_substantive_answer():
    gt = [_gt("Acme", "Does the company have tooling capability?", "Yes")]
    ai = [_ai("Acme", "Does the company have tooling capability?", "Yes"),
          _ai("Acme", "Does the company have tooling capability?", "Not disclosed")]
    r = evaluate(gt, ai, semantic=False)
    assert r.overall["TP"] == 1 and r.overall["FP"] == 0
    assert r.overall["suppressed_nulls"] == 1
    print("OK test_page_local_null_suppressed_beside_substantive_answer passed")


def test_only_null_claims_still_null_match():
    # A cell whose ONLY AI claims are nulls must keep them: a GT null is a
    # true negative the pipeline correctly reported (null_match), and
    # suppression must not fire without a substantive claim in the cell.
    gt = [_gt("Acme", "What is the company's yearly revenue?",
              "None (not disclosed)")]
    ai = [_ai("Acme", "What is the company's yearly revenue?", "Not disclosed")]
    r = evaluate(gt, ai, semantic=False)
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert "null_match" in verdicts
    assert r.overall["FP"] == 0
    assert r.overall["suppressed_nulls"] == 0
    print("OK test_only_null_claims_still_null_match passed")


def test_suppression_makes_gt_null_a_genuine_miss():
    # GT says not-disclosed but the tool's displayed verdict is a substantive
    # "Yes": the Yes is the answer being graded (FP), and the page-local null
    # must NOT sneak in as a null_match that would also credit the cell.
    gt = [_gt("Acme", "Does the company have tooling capability?",
              "None (not disclosed)")]
    ai = [_ai("Acme", "Does the company have tooling capability?", "Yes"),
          _ai("Acme", "Does the company have tooling capability?", "Not disclosed")]
    r = evaluate(gt, ai, semantic=False)
    verdicts = [p.verdict for c in r.cells for p in c.gt_pairs]
    assert "null_match" not in verdicts
    assert r.overall["FP"] == 1
    assert r.overall["suppressed_nulls"] == 1
    print("OK test_suppression_makes_gt_null_a_genuine_miss passed")


def test_multiple_page_local_nulls_all_suppressed():
    gt = [_gt("Acme", "In which country/countries does manufacturing take place?",
              "United Kingdom", is_list=True)]
    ai = [_ai("Acme", "In which country/countries does manufacturing take place?",
              "United Kingdom"),
          _ai("Acme", "In which country/countries does manufacturing take place?",
              "Not disclosed"),
          _ai("Acme", "In which country/countries does manufacturing take place?",
              "not disclosed on site")]
    r = evaluate(gt, ai, semantic=False)
    assert r.overall["TP"] == 1 and r.overall["FP"] == 0
    assert r.overall["suppressed_nulls"] == 2
    print("OK test_multiple_page_local_nulls_all_suppressed passed")


def test_partial_gt_excludes_uncovered_entities():
    # A GT that covers one entity must not count another entity's claims as
    # hallucinations — they are unmeasured, not wrong (the CMO partial-GT
    # case: 5 analyst rows vs 57 pipeline entities).
    gt = [_gt("Covered Co", "Does the company have tooling capability?", "Yes")]
    ai = [_ai("Covered Co", "Does the company have tooling capability?", "Yes"),
          _ai("Uncovered Co", "Does the company have tooling capability?", "No"),
          _ai("Uncovered Co", "Where is the company headquarters located?", "Paris")]
    r = evaluate(gt, ai, semantic=False)
    assert r.overall["TP"] == 1 and r.overall["FP"] == 0
    assert r.overall["entities"] == 1
    print("OK test_partial_gt_excludes_uncovered_entities passed")
