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

class _FakeHttpxResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def test_robots_disallow_blocks_and_caches(monkeypatch):
    pp._robots_cache.clear()
    call_count = {"n": 0}

    def fake_get(url, **kwargs):
        call_count["n"] += 1
        return _FakeHttpxResponse(200, "User-agent: *\nDisallow: /")

    monkeypatch.setattr(pp.httpx, "get", fake_get)
    monkeypatch.setattr(pp, "CRAWL_RESPECT_ROBOTS", True)

    assert pp.robots_allows("https://blocked.com/page") is False
    assert pp.robots_allows("https://blocked.com/other") is False
    assert call_count["n"] == 1, "robots.txt must be fetched once per domain, then cached"
    print("OK test_robots_disallow_blocks_and_caches passed")


def test_robots_unreadable_treated_as_allow(monkeypatch):
    pp._robots_cache.clear()

    def raise_error(*args, **kwargs):
        raise OSError("unreachable")

    monkeypatch.setattr(pp.httpx, "get", raise_error)
    monkeypatch.setattr(pp, "CRAWL_RESPECT_ROBOTS", True)
    assert pp.robots_allows("https://norobots.com/page") is True
    print("OK test_robots_unreadable_treated_as_allow passed")


def test_robots_403_treated_as_allow(monkeypatch):
    """WAF returning 403 on robots.txt fetch must be allow, not disallow_all.

    The original urllib-based implementation set disallow_all=True on any 403,
    causing false-positive blocks on domains whose robots.txt actually reads
    "Allow: /" but whose WAF rejects Python's default User-Agent.
    """
    pp._robots_cache.clear()
    monkeypatch.setattr(pp.httpx, "get", lambda *a, **kw: _FakeHttpxResponse(403))
    monkeypatch.setattr(pp, "CRAWL_RESPECT_ROBOTS", True)
    assert pp.robots_allows("https://waf-protected.com/page") is True
    print("OK test_robots_403_treated_as_allow passed")


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


# ── hybrid backend (static-first, escalate to pooled render) ─────────────────

_GOOD_HTML = ("<html><body><main>" + "<p>real content here. </p>" * 40 +
              '</main><a href="/about">About us</a></body></html>')
_JUNK_HTML = '<html><body><a href="/x">nav</a></body></html>'


def _hybrid_cfg():
    return Config(acquire_tool="playwright_pooled_hybrid")


class _FakeStaticResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def _quiet_politeness(monkeypatch):
    """Politeness primitives pass through instantly; behaviour covered by their own tests."""
    monkeypatch.setattr(pp, "robots_allows", lambda url: True)
    monkeypatch.setattr(pp, "wait_for_domain_slot", lambda url, delay_s=None: 0.0)


def test_hybrid_static_pass_never_launches_browser(monkeypatch):
    _quiet_politeness(monkeypatch)
    monkeypatch.setattr(f.httpx, "get", lambda url, **kw: _FakeStaticResponse(_GOOD_HTML))

    def browser_must_not_launch(url, **kw):
        raise AssertionError("static pass must not escalate to the browser")
    monkeypatch.setattr(pp, "fetch_rendered_html", browser_must_not_launch)

    text, html, prov = f.fetch_page_with_provenance("https://static-ok.com/x", _hybrid_cfg())
    assert prov["backend"] == "pooled_hybrid_static" and prov["render_fallback"] is False
    assert prov["gate_passed"] is True and prov["gate_reason"] == ""
    assert "real content here" in text and "/about" in html
    print("OK test_hybrid_static_pass_never_launches_browser passed")


def test_hybrid_escalates_to_render_on_gate_fail(monkeypatch):
    _quiet_politeness(monkeypatch)
    monkeypatch.setattr(f.httpx, "get", lambda url, **kw: _FakeStaticResponse(_JUNK_HTML))
    monkeypatch.setattr(pp, "fetch_rendered_html", lambda url, **kw: _GOOD_HTML)

    text, html, prov = f.fetch_page_with_provenance("https://js-site.com/x", _hybrid_cfg())
    assert prov["backend"] == "pooled_hybrid_render" and prov["render_fallback"] is True
    assert prov["gate_passed"] is True
    assert "real content here" in text, "content must come from the rendered DOM"
    print("OK test_hybrid_escalates_to_render_on_gate_fail passed")


def test_hybrid_escalates_to_render_on_static_error(monkeypatch):
    _quiet_politeness(monkeypatch)

    def static_blocked(url, **kw):
        raise OSError("connection reset")
    monkeypatch.setattr(f.httpx, "get", static_blocked)
    monkeypatch.setattr(pp, "fetch_rendered_html", lambda url, **kw: _GOOD_HTML)

    text, html, prov = f.fetch_page_with_provenance("https://httpx-blocked.com/x", _hybrid_cfg())
    assert prov["backend"] == "pooled_hybrid_render" and prov["gate_passed"] is True
    assert "real content here" in text
    print("OK test_hybrid_escalates_to_render_on_static_error passed")


def test_hybrid_robots_disallowed_makes_no_requests(monkeypatch):
    monkeypatch.setattr(pp, "robots_allows", lambda url: False)

    def no_request_allowed(*a, **kw):
        raise AssertionError("robots-disallowed URL must not be requested at all")
    monkeypatch.setattr(f.httpx, "get", no_request_allowed)
    monkeypatch.setattr(pp, "fetch_rendered_html", no_request_allowed)

    text, html, prov = f.fetch_page_with_provenance("https://blocked.com/x", _hybrid_cfg())
    assert text == "" and prov["gate_passed"] is False
    assert prov["gate_reason"] == "robots_disallowed"
    print("OK test_hybrid_robots_disallowed_makes_no_requests passed")


def test_hybrid_render_failure_keeps_static_content(monkeypatch):
    _quiet_politeness(monkeypatch)
    monkeypatch.setattr(f.httpx, "get", lambda url, **kw: _FakeStaticResponse(_JUNK_HTML))

    def render_crashes(url, **kw):
        raise RuntimeError("browser crashed")
    monkeypatch.setattr(pp, "fetch_rendered_html", render_crashes)

    text, html, prov = f.fetch_page_with_provenance("https://render-fails.com/x", _hybrid_cfg())
    assert prov["backend"] == "pooled_hybrid_static" and prov["gate_passed"] is False
    assert "render_error" in prov["gate_reason"], "render failure must stay recorded"
    assert html == _JUNK_HTML, "gate-failed static content must be kept, not lost"
    print("OK test_hybrid_render_failure_keeps_static_content passed")


def test_hybrid_backend_is_valid_and_selectable():
    assert "playwright_pooled_hybrid" in f._VALID_BACKENDS
    print("OK test_hybrid_backend_is_valid_and_selectable passed")


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
