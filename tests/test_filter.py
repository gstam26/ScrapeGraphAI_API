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


if __name__ == "__main__":
    test_passthrough_routes_below_threshold_column(None)
    test_threshold_excludes_below_threshold_column(None)
    test_passthrough_multi_column_all_routed(None)
    print("\nAll filter tests passed!")
