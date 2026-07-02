"""
Offline tests for the playwright_pooled backend's politeness gate and dispatch.
No browser, no network — the pool itself is exercised by the live bake-off.
"""
import time

import src.acquire.playwright_pool as pp
from src.acquire import fetcher as f
from models import Config


# ── per-domain rate limit ─────────────────────────────────────────────────────

def test_domain_slot_enforces_delay_same_domain():
    pp._last_request_at.clear()
    t0 = time.monotonic()
    pp.wait_for_domain_slot("https://www.example.com/a", delay_s=0.2)
    pp.wait_for_domain_slot("https://example.com/b", delay_s=0.2)  # same domain (www stripped)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.2, f"second request to same domain must wait (elapsed {elapsed:.3f}s)"
    print("OK test_domain_slot_enforces_delay_same_domain passed")


def test_domain_slot_independent_domains_do_not_wait():
    pp._last_request_at.clear()
    t0 = time.monotonic()
    pp.wait_for_domain_slot("https://a.com/x", delay_s=0.5)
    pp.wait_for_domain_slot("https://b.com/x", delay_s=0.5)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.3, f"different domains must not serialise (elapsed {elapsed:.3f}s)"
    print("OK test_domain_slot_independent_domains_do_not_wait passed")


# ── robots.txt ────────────────────────────────────────────────────────────────

class _FakeRobots:
    """Stands in for RobotFileParser; records reads, answers can_fetch."""
    instances = 0

    def __init__(self, allow=True, read_raises=False):
        self._allow = allow
        self._read_raises = read_raises

    def set_url(self, url):
        pass

    def read(self):
        type(self).instances += 1
        if self._read_raises:
            raise OSError("unreachable")

    def can_fetch(self, ua, url):
        return self._allow


def test_robots_disallow_blocks_and_caches(monkeypatch):
    pp._robots_cache.clear()
    _FakeRobots.instances = 0
    monkeypatch.setattr(pp.robotparser, "RobotFileParser", lambda: _FakeRobots(allow=False))
    monkeypatch.setattr(pp, "CRAWL_RESPECT_ROBOTS", True)

    assert pp.robots_allows("https://blocked.com/page") is False
    assert pp.robots_allows("https://blocked.com/other") is False
    assert _FakeRobots.instances == 1, "robots.txt must be fetched once per domain, then cached"
    print("OK test_robots_disallow_blocks_and_caches passed")


def test_robots_unreadable_treated_as_allow(monkeypatch):
    pp._robots_cache.clear()
    monkeypatch.setattr(pp.robotparser, "RobotFileParser", lambda: _FakeRobots(read_raises=True))
    monkeypatch.setattr(pp, "CRAWL_RESPECT_ROBOTS", True)
    assert pp.robots_allows("https://norobots.com/page") is True
    print("OK test_robots_unreadable_treated_as_allow passed")


def test_robots_respect_flag_off_allows_everything(monkeypatch):
    monkeypatch.setattr(pp, "CRAWL_RESPECT_ROBOTS", False)
    assert pp.robots_allows("https://anything.com/x") is True
    print("OK test_robots_respect_flag_off_allows_everything passed")


# ── backend dispatch & provenance ─────────────────────────────────────────────

def _cfg():
    return Config(acquire_tool="playwright_pooled")


def test_pooled_backend_robots_disallowed_provenance(monkeypatch):
    def raise_disallowed(url, timeout_ms=15000, settle_ms=2000):
        raise pp.RobotsDisallowed(url)
    monkeypatch.setattr(pp, "fetch_rendered_html", raise_disallowed)

    text, html, prov = f.fetch_page_with_provenance("https://blocked.com/x", _cfg())
    assert text == "" and prov["backend"] == "playwright_pooled"
    assert prov["gate_passed"] is False and prov["gate_reason"] == "robots_disallowed"
    print("OK test_pooled_backend_robots_disallowed_provenance passed")


def test_pooled_backend_returns_rendered_html_for_discovery(monkeypatch):
    body = "<html><body><main>" + "<p>real content here. </p>" * 40 + \
           '</main><a href="/about">About us</a></body></html>'
    monkeypatch.setattr(pp, "fetch_rendered_html", lambda url, **kw: body)

    text, html, prov = f.fetch_page_with_provenance("https://ok.com/x", _cfg())
    assert prov["backend"] == "playwright_pooled"
    assert html is not None and "/about" in html, "rendered DOM must be returned for link discovery"
    assert "real content here" in text
    print("OK test_pooled_backend_returns_rendered_html_for_discovery passed")


def test_pooled_backend_is_valid_and_selectable():
    assert "playwright_pooled" in f._VALID_BACKENDS
    print("OK test_pooled_backend_is_valid_and_selectable passed")


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
