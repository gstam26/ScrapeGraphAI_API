"""
Smoke tests for the Acquire layer.

Requires a live network connection — three real URLs are fetched.
Run with: python test_acquire_smoke.py
"""

from src.acquire import acquire, FetchedPage
from models import Config

URLS = [
    "https://example.com",
    "https://www.iana.org/domains/reserved",
    "https://www.python.org",
]


def test_acquire_returns_fetched_pages():
    cfg = Config(acquire_tool="requests")
    pages = acquire(URLS, cfg)

    assert isinstance(pages, list), "acquire() must return a list"
    assert len(pages) == len(URLS), f"Expected {len(URLS)} pages, got {len(pages)}"

    for page in pages:
        assert isinstance(page, FetchedPage), f"Expected FetchedPage, got {type(page)}"
        assert page.url in URLS, f"Unexpected URL: {page.url}"
        assert page.status in ("ok", "cached"), f"Bad status {page.status!r} for {page.url}"
        assert isinstance(page.markdown, str), "markdown must be a string"
        assert len(page.markdown) > 0, f"Empty markdown for {page.url}"
        assert page.parent_url is None, "parent_url must be None at depth=0"

    print(f"✓ test_acquire_returns_fetched_pages passed ({len(pages)} pages)")


def test_cache_hit_on_second_call():
    cfg = Config(acquire_tool="requests")
    acquire(URLS, cfg)  # populate cache

    pages = acquire(URLS, cfg)

    for page in pages:
        assert page.status == "cached", f"Expected 'cached' on second call, got {page.status!r}"

    print("✓ test_cache_hit_on_second_call passed")


def test_config_defaults():
    cfg = Config()
    assert cfg.acquire_tool == "requests"
    assert cfg.cache_dir == "cache"
    assert cfg.request_timeout == 30
    assert "User-Agent" in cfg.request_headers
    print("✓ test_config_defaults passed")


def test_unknown_tool_raises():
    cfg = Config(acquire_tool="unknown_tool")
    try:
        acquire(URLS[:1], cfg)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "unknown_tool" in str(e)
    print("✓ test_unknown_tool_raises passed")


if __name__ == "__main__":
    test_config_defaults()
    test_unknown_tool_raises()
    test_acquire_returns_fetched_pages()
    test_cache_hit_on_second_call()
    print("\n✅ All acquire smoke tests passed!")
