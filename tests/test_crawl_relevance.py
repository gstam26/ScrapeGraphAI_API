"""
Unit tests for dynamic crawl query building and bounded fallback.
No network calls.
"""

from models import ColumnSpec
from src.acquire.crawler import (
    _FALLBACK_TOP_K,
    _FALLBACK_MAX_DEPTH,
    _select_links_to_follow,
    build_crawl_query,
)
from src.acquire.link_scorer import (
    score_links,
    _clean_scoring_text,
    _structural_penalty,
)
from src.acquire.acquire_models import LinkCandidate


def _make_candidate(url: str, anchor: str, score: float = 0.0) -> LinkCandidate:
    c = LinkCandidate(url=url, anchor_text=anchor, depth=1)
    c.score = score
    return c


# ── Test 1: query includes all three tiers ─────────────────────────────────────

def test_build_crawl_query_excludes_entity_terms():
    cols = [ColumnSpec(name="carbon footprint", instruction="report year")]
    query = build_crawl_query(cols, entities=["Oatly"])

    assert "carbon" in query, "question term missing"
    assert "footprint" in query, "question term missing"
    assert "report" in query, "instruction term missing"
    assert "year" in query, "instruction term missing"
    assert "oatly" not in query, "entity terms are handled by link hygiene, not relevance scoring"

    # question weight > instruction weight
    assert query["carbon"] > query["report"], "question terms must outweigh instruction terms"

    print("OK test_build_crawl_query_excludes_entity_terms passed")


# ── Test 2: no hardcoded sustainability / generic boosts ──────────────────────

def test_no_hardcoded_terms_in_unrelated_query():
    cols = [ColumnSpec(name="revenue growth", instruction="annual")]
    query = build_crawl_query(cols, entities=[])

    for word in ("sustainability", "esg", "carbon", "emissions", "climate",
                 "about", "company", "overview", "story", "mission",
                 "products", "services"):
        assert word not in query, f"hardcoded term {word!r} leaked into query"

    print("OK test_no_hardcoded_terms_in_unrelated_query passed")


def test_score_links_no_hardcoded_boost():
    cols = [ColumnSpec(name="warranty period", instruction=None)]
    query = build_crawl_query(cols, entities=["Acme"])

    candidates = [
        LinkCandidate(url="https://example.com/sustainability", anchor_text="Sustainability", depth=1),
        LinkCandidate(url="https://example.com/warranty", anchor_text="Warranty policy", depth=1),
    ]
    scored = score_links(candidates, query)
    by_url = {c.url: c.score for c in scored}

    # sustainability is not in the query — must score 0
    assert by_url["https://example.com/sustainability"] == 0.0
    # warranty is a question term — must score highest
    assert by_url["https://example.com/warranty"] == 1.0

    print("OK test_score_links_no_hardcoded_boost passed")


def test_experimental_cleaning_removes_generic_boilerplate():
    text = "![Logo](logo.png) Skip links Accessibility menu Warranty details"
    cleaned = _clean_scoring_text(
        text,
        {"skip", "links", "accessibility", "menu", "logo", "image"},
    )

    assert "warranty" in cleaned
    assert "details" in cleaned
    assert "skip" not in cleaned
    assert "accessibility" not in cleaned
    assert "logo" not in cleaned

    print("OK test_experimental_cleaning_removes_generic_boilerplate passed")


def test_experimental_penalty_uses_generic_structure_only():
    informational = LinkCandidate(
        url="https://example.com/reports/warranty",
        anchor_text="Warranty report",
        depth=1,
        context="Detailed warranty evidence and policy information",
    )
    navigational = LinkCandidate(
        url="https://example.com/products/category/list",
        anchor_text="Shop",
        depth=1,
        context="Browse products sort filter collection",
    )
    nav_terms = {"shop", "products", "category", "collection", "browse", "filter", "sort"}

    info_penalty = _structural_penalty(informational, "warranty report evidence", nav_terms)
    nav_penalty = _structural_penalty(navigational, "shop products category filter", nav_terms)

    assert nav_penalty > info_penalty

    print("OK test_experimental_penalty_uses_generic_structure_only passed")


# ── Test 3: bounded fallback ──────────────────────────────────────────────────

def test_fallback_follows_top_k_when_nothing_passes_threshold():
    candidates = [_make_candidate(f"https://x.com/p{i}", "click here", score=0.05) for i in range(10)]

    result = _select_links_to_follow(candidates, min_score=0.5, depth=0)
    assert 0 < len(result) <= _FALLBACK_TOP_K, "fallback must follow at most _FALLBACK_TOP_K links"

    print("OK test_fallback_follows_top_k_when_nothing_passes_threshold passed")


def test_fallback_disabled_beyond_max_depth():
    candidates = [_make_candidate(f"https://x.com/p{i}", "click", score=0.05) for i in range(5)]

    result = _select_links_to_follow(candidates, min_score=0.5, depth=_FALLBACK_MAX_DEPTH + 1)
    assert result == [], "fallback must not fire past _FALLBACK_MAX_DEPTH"

    print("OK test_fallback_disabled_beyond_max_depth passed")


def test_normal_threshold_takes_precedence_over_fallback():
    above = _make_candidate("https://x.com/good", "warranty policy", score=0.9)
    below = [_make_candidate(f"https://x.com/p{i}", "click", score=0.05) for i in range(8)]
    candidates = [above] + below

    result = _select_links_to_follow(candidates, min_score=0.5, depth=0)
    assert len(result) == 1 and result[0].url == "https://x.com/good", \
        "when above-threshold links exist, fallback must not add extras"

    print("OK test_normal_threshold_takes_precedence_over_fallback passed")


if __name__ == "__main__":
    test_build_crawl_query_excludes_entity_terms()
    test_no_hardcoded_terms_in_unrelated_query()
    test_score_links_no_hardcoded_boost()
    test_experimental_cleaning_removes_generic_boilerplate()
    test_experimental_penalty_uses_generic_structure_only()
    test_fallback_follows_top_k_when_nothing_passes_threshold()
    test_fallback_disabled_beyond_max_depth()
    test_normal_threshold_takes_precedence_over_fallback()
    print("\nAll crawl relevance tests passed!")
