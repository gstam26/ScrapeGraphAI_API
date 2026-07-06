"""
Unit tests for dynamic crawl query building and bounded fallback.
No network calls.
"""

from models import ColumnSpec
from src.acquire.crawler import (
    _FALLBACK_TOP_K,
    _FALLBACK_MAX_DEPTH,
    _discover_links_from_markdown,
    _locale_key,
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


# ── Junk-link filtering ────────────────────────────────────────────────────────

def test_image_urls_never_become_crawl_candidates():
    """Regression for the 2026-07-06 finding: cached-markdown link discovery
    matches the [alt](url) inside markdown images ![alt](url), so image URLs
    became crawl candidates — and .avif was missing from _JUNK_EXTS, producing
    live Firecrawl calls against image files during George's validation
    re-run. Every common image extension must be junk-filtered, on both the
    markdown and HTML discovery paths."""
    from src.acquire.crawler import _discover_links_from_markdown, _is_junk_link

    image_exts = [".avif", ".heic", ".heif", ".bmp", ".tif", ".tiff", ".jxl",
                  ".apng", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]
    for ext in image_exts:
        assert _is_junk_link(f"https://example.com/media/photo{ext}"), ext
        assert _is_junk_link(f"https://example.com/media/photo{ext.upper()}"), ext
        assert _is_junk_link(f"https://example.com/media/photo{ext}/"), ext

    # The observed failure shape: a markdown image inline in cached page text.
    text = (
        "Welcome. ![hero banner](https://example.com/img/hero.avif) "
        "Read [about us](https://example.com/about) for more."
    )
    candidates = _discover_links_from_markdown(
        "https://example.com", "https://example.com", depth=1, text=text)
    urls = [c.url for c in candidates]
    assert "https://example.com/about" in urls
    assert not any(u.endswith(".avif") for u in urls), urls
    print("OK test_image_urls_never_become_crawl_candidates passed")


# ── Locale-variant dedup ───────────────────────────────────────────────────────

def test_locale_key_collapses_language_homepages():
    """Translated homepages (validation-run waste: Bruker, Metrohm, QuidelOrtho)
    share one key; www prefix is ignored."""
    assert _locale_key("https://www.bruker.com/fr.html") == _locale_key("https://www.bruker.com/de.html")
    assert _locale_key("https://www.bruker.com/en.html") == _locale_key("https://bruker.com/ko.html")
    assert _locale_key("https://www.metrohm.com/th_th.html") == _locale_key("https://www.metrohm.com/ko_kr.html")
    assert _locale_key("https://www.quidelortho.com/de/de") == _locale_key("https://www.quidelortho.com/jp/ja")
    print("OK test_locale_key_collapses_language_homepages passed")


def test_locale_key_keeps_distinct_content_pages():
    """Sites that nest ALL content under a locale prefix (aladdinsci /us_en/,
    sebia /en-us/) must NOT have distinct pages collapsed; query strings that
    address different content (horiba index.php?product=N) stay distinct."""
    assert _locale_key("https://www.aladdinsci.com/us_en/contact") != \
        _locale_key("https://www.aladdinsci.com/us_en/life-sciences.html")
    assert _locale_key("https://www.sebia.com/en-us/resources") != \
        _locale_key("https://www.sebia.com/en-us/technologies/gel-electrophoresis")
    assert _locale_key("https://www.horiba.com/index.php?id=128&product=1938") != \
        _locale_key("https://www.horiba.com/index.php?id=128&product=2001")
    # 3-letter segments are not locales
    assert _locale_key("https://www.horiba.com/usa/healthcare") != \
        _locale_key("https://www.horiba.com/fra/healthcare")
    print("OK test_locale_key_keeps_distinct_content_pages passed")


def test_locale_key_released_after_fetch_failure(monkeypatch):
    """A locale-key claim must be released if the claimed URL's fetch fails,
    so a same-key sibling discovered later on a different page is still
    fetched rather than silently dropped as a 'duplicate' of a page that was
    never actually acquired (2026-07-03 code review: this was previously a
    permanent, silent coverage-loss bug — the threshold-skip path a few lines
    above crawl_entity's except block releases the same way, via the same
    one-line discard() call, so this test covers both release sites)."""
    import src.acquire.crawler as crawler_mod
    from models import Config, PageDoc

    monkeypatch.setattr(crawler_mod, "SCORER_TOOL", "bm25")  # force non-ollama BM25 path, no network
    monkeypatch.setattr(crawler_mod, "CRAWL_LOCALE_DEDUP", True)

    seed = "https://x.com"
    fail_url = "https://x.com/de/contact"      # depth 1: claimed, then fetch fails
    other_url = "https://x.com/other-page"     # depth 1: fetches fine, leads to the sibling
    sibling_url = "https://x.com/en/contact"   # depth 2: same locale key as fail_url

    def fake_discover(page_url, start_url, depth, html=None, page_text=None, cfg=None):
        if page_url == seed:
            return [
                LinkCandidate(url=fail_url, anchor_text="contact", depth=depth, context=""),
                LinkCandidate(url=other_url, anchor_text="other", depth=depth, context=""),
            ]
        if page_url == other_url:
            return [LinkCandidate(url=sibling_url, anchor_text="contact", depth=depth, context="")]
        return []

    def fake_acquire(url, cfg):
        if url == fail_url:
            raise RuntimeError("simulated transient fetch failure")
        return PageDoc(url=url, text="content", html=None)

    monkeypatch.setattr(crawler_mod, "_discover_links", fake_discover)
    monkeypatch.setattr(crawler_mod, "_acquire_page_cfg", fake_acquire)

    cfg = Config(acquire_tool="requests", crawl_min_score=0.0, crawl_max_pages=10)
    columns = [ColumnSpec(name="contact info")]

    doc = crawler_mod.crawl_entity(seed, columns, cfg, max_depth=2)
    fetched = {p.url for p in doc.pages}

    assert other_url in fetched
    assert fail_url not in fetched
    assert sibling_url in fetched, (
        "sibling must be fetched once the failed variant's locale-key claim is released"
    )
    print("OK test_locale_key_released_after_fetch_failure passed")


def test_discovery_no_longer_truncates_before_scoring():
    """The CRAWL_MAX_LINKS_PER_PAGE cap must not be applied at discovery time —
    a footer About link past the 30th anchor has to reach the scorer."""
    links = "\n".join(f"[product {i}](https://x.com/product-{i})" for i in range(40))
    text = links + "\n[About us](https://x.com/about)"
    candidates = _discover_links_from_markdown("https://x.com", "https://x.com", 1, text)
    assert len(candidates) == 41, f"expected all 41 candidates, got {len(candidates)}"
    assert any(c.url.endswith("/about") for c in candidates), "41st (footer) link must survive discovery"
    print("OK test_discovery_no_longer_truncates_before_scoring passed")


if __name__ == "__main__":
    test_build_crawl_query_excludes_entity_terms()
    test_no_hardcoded_terms_in_unrelated_query()
    test_score_links_no_hardcoded_boost()
    test_experimental_cleaning_removes_generic_boilerplate()
    test_experimental_penalty_uses_generic_structure_only()
    test_fallback_follows_top_k_when_nothing_passes_threshold()
    test_fallback_disabled_beyond_max_depth()
    test_normal_threshold_takes_precedence_over_fallback()
    test_locale_key_collapses_language_homepages()
    test_locale_key_keeps_distinct_content_pages()
    test_locale_key_released_after_fetch_failure()
    test_discovery_no_longer_truncates_before_scoring()
    print("\nAll crawl relevance tests passed!")
