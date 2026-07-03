"""
Unit tests for filter_page passthrough vs threshold modes.

score_page_columns is monkeypatched to avoid the Ollama embedding endpoint.
"""

import src.filter as filter_mod
from models import ColumnSpec, PageDoc


def _page() -> PageDoc:
    return PageDoc(url="http://example.com/our-story", text="welcome to our story page " * 40)


def _columns() -> list[ColumnSpec]:
    return [ColumnSpec(name="Sustainability claims")]


def test_passthrough_routes_below_threshold_column(monkeypatch):
    """In passthrough mode a column scoring below threshold is still included,
    the score is still logged, and the reason is 'passthrough'."""
    monkeypatch.setattr(filter_mod, "FILTER_MODE", "passthrough")
    monkeypatch.setattr(
        filter_mod, "score_page_columns",
        lambda text, cols: {col.name: 0.4962 for col in cols},
    )

    diag: dict = {}
    routed = filter_mod.filter_page(_page(), _columns(), diag=diag)

    assert "Sustainability claims" in routed.relevant_columns

    log = diag["filter_log"]
    assert len(log) == 1
    row = log[0]
    assert row["included"] is True
    assert row["reason"] == "passthrough"
    assert row["embedding_score"] == 0.4962   # score still recorded
    print("OK test_passthrough_routes_below_threshold_column passed")


def test_threshold_excludes_below_threshold_column(monkeypatch):
    """In threshold mode a column scoring below threshold (no keyword match) is
    excluded when at least one other column clears the threshold — preventing the
    empty-set fallback that would otherwise route everything.

    This mirrors the real scenario: Ripple our-story has Sustainability claims at
    0.4962 (excluded) while Parent company clears 0.55 (included)."""
    monkeypatch.setattr(filter_mod, "FILTER_MODE", "threshold")
    scores = {
        "Sustainability claims": 0.4962,  # below threshold, no keyword match
        "Parent company": 0.8000,         # above threshold → prevents fallback
    }
    monkeypatch.setattr(
        filter_mod, "score_page_columns",
        lambda text, cols: {col.name: scores[col.name] for col in cols},
    )
    # Page text contains neither "sustainability"/"claims" nor "parent"/"company".
    page = PageDoc(url="http://example.com/our-story", text="welcome to our story page " * 40)
    columns = [ColumnSpec(name="Sustainability claims"), ColumnSpec(name="Parent company")]

    diag: dict = {}
    routed = filter_mod.filter_page(page, columns, diag=diag)

    assert "Sustainability claims" not in routed.relevant_columns
    assert "Parent company" in routed.relevant_columns

    log_by_col = {row["column"]: row for row in diag["filter_log"]}
    sc_row = log_by_col["Sustainability claims"]
    assert sc_row["included"] is False
    assert sc_row["reason"] == "below_threshold"
    assert sc_row["embedding_score"] == 0.4962
    pc_row = log_by_col["Parent company"]
    assert pc_row["included"] is True
    assert pc_row["reason"] == "embedding_threshold"
    print("OK test_threshold_excludes_below_threshold_column passed")


def test_passthrough_multi_column_all_routed(monkeypatch):
    """All columns are routed in passthrough regardless of their individual scores."""
    monkeypatch.setattr(filter_mod, "FILTER_MODE", "passthrough")
    scores = {
        "Sustainability claims": 0.4962,
        "Plant milk types": 0.3100,
        "Parent company": 0.7800,
    }
    monkeypatch.setattr(
        filter_mod, "score_page_columns",
        lambda text, cols: {col.name: scores[col.name] for col in cols},
    )

    columns = [
        ColumnSpec(name="Sustainability claims"),
        ColumnSpec(name="Plant milk types"),
        ColumnSpec(name="Parent company"),
    ]
    diag: dict = {}
    routed = filter_mod.filter_page(_page(), columns, diag=diag)

    assert routed.relevant_columns == {"Sustainability claims", "Plant milk types", "Parent company"}
    assert all(row["reason"] == "passthrough" for row in diag["filter_log"])
    assert all(row["included"] is True for row in diag["filter_log"])
    print("OK test_passthrough_multi_column_all_routed passed")


# ── Instruction-aware routing queries (query_text / score_page_columns) ──────

_INSTRUCTION = (
    "In which country does the company conduct its R&D? "
    "Check headquarters, locations, laboratories, or about pages."
)


def _capture_embed(monkeypatch) -> list[list[str]]:
    """Monkeypatch src.filter.embed_batch to record inputs; returns the log.

    Also resets the module-level question-embedding cache so each test starts
    cold. Returned vectors are deterministic dummies of the right count.
    """
    calls: list[list[str]] = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(filter_mod, "embed_batch", fake_embed)
    monkeypatch.setattr(filter_mod, "_question_emb_cache", {})
    return calls


def test_query_text_includes_instruction_when_flag_on(monkeypatch):
    """With QUERY_INCLUDES_INSTRUCTION=True the embedded query for a column
    carrying an instruction contains BOTH name and instruction; a column
    without an instruction embeds the name only."""
    monkeypatch.setattr(filter_mod, "QUERY_INCLUDES_INSTRUCTION", True)
    calls = _capture_embed(monkeypatch)

    columns = [
        ColumnSpec(name="R&D location", instruction=_INSTRUCTION),
        ColumnSpec(name="Recent news"),  # no instruction
    ]
    filter_mod.score_page_columns("some page text about laboratories", columns)

    first_batch = calls[0]
    rd_query, news_query = first_batch[0], first_batch[1]
    assert "R&D location" in rd_query
    assert _INSTRUCTION in rd_query
    assert news_query.endswith("Recent news")   # name only, no instruction
    print("OK test_query_text_includes_instruction_when_flag_on passed")


def test_query_text_name_only_when_flag_off(monkeypatch):
    """With QUERY_INCLUDES_INSTRUCTION=False the old name-only queries are
    restored even for columns that carry an instruction (A-B comparison)."""
    monkeypatch.setattr(filter_mod, "QUERY_INCLUDES_INSTRUCTION", False)
    calls = _capture_embed(monkeypatch)

    columns = [ColumnSpec(name="R&D location", instruction=_INSTRUCTION)]
    filter_mod.score_page_columns("some page text", columns)

    rd_query = calls[0][0]
    assert rd_query.endswith("R&D location")
    assert _INSTRUCTION not in rd_query
    print("OK test_query_text_name_only_when_flag_off passed")


def test_question_emb_cache_keys_on_query_text_not_name(monkeypatch):
    """Two column sets sharing NAMES but differing INSTRUCTIONS must not share
    cached question embeddings: the second set's questions are re-embedded
    (cache key is the query-text tuple, not the name tuple)."""
    monkeypatch.setattr(filter_mod, "QUERY_INCLUDES_INSTRUCTION", True)
    calls = _capture_embed(monkeypatch)

    cols_v1 = [ColumnSpec(name="R&D location", instruction="Check the about pages.")]
    cols_v2 = [ColumnSpec(name="R&D location", instruction="Check the careers pages.")]

    filter_mod.score_page_columns("page text", cols_v1)
    filter_mod.score_page_columns("page text", cols_v2)

    all_texts = [t for batch in calls for t in batch]
    assert any("Check the about pages." in t for t in all_texts)
    # Stale-cache bug would skip this second question embedding entirely:
    assert any("Check the careers pages." in t for t in all_texts)
    # Second call embedded questions + chunks together (cache miss), not chunks only.
    assert len(calls[1]) == 1 + 1  # 1 question + 1 chunk
    print("OK test_question_emb_cache_keys_on_query_text_not_name passed")


def test_keyword_gate_uses_name_only_not_instruction(monkeypatch):
    """The keyword gate keys on the column NAME only: instruction words that
    appear in the page text must not fire the gate (they are too generic —
    'check', 'pages', 'company' would over-fire on almost every page)."""
    monkeypatch.setattr(filter_mod, "FILTER_MODE", "threshold")
    scores = {
        "Sustainability claims": 0.40,  # below threshold
        "Parent company": 0.80,         # above threshold -> prevents fallback
    }
    monkeypatch.setattr(
        filter_mod, "score_page_columns",
        lambda text, cols: {col.name: scores[col.name] for col in cols},
    )
    # Page text contains the INSTRUCTION words ("welcome", "story") but none of
    # the name keywords ("sustainability"/"claims").
    page = PageDoc(url="http://example.com/our-story", text="welcome to our story page " * 40)
    columns = [
        ColumnSpec(
            name="Sustainability claims",
            instruction="Check the welcome page and the story page.",
        ),
        ColumnSpec(name="Parent company"),
    ]

    diag: dict = {}
    routed = filter_mod.filter_page(page, columns, diag=diag)

    assert "Sustainability claims" not in routed.relevant_columns
    log_by_col = {row["column"]: row for row in diag["filter_log"]}
    assert log_by_col["Sustainability claims"]["keyword_gate"] is False
    assert log_by_col["Sustainability claims"]["reason"] == "below_threshold"
    print("OK test_keyword_gate_uses_name_only_not_instruction passed")


if __name__ == "__main__":
    test_passthrough_routes_below_threshold_column(None)
    test_threshold_excludes_below_threshold_column(None)
    test_passthrough_multi_column_all_routed(None)
    print("\nAll filter tests passed!")
