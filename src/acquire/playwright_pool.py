"""Pooled Playwright fetching with a politeness gate.

Self-hosted alternative to Firecrawl (brain/proposals/firecrawl-replacement.md).
Unlike Firecrawl, requests come from THIS machine's IP, so politeness is not
optional here — it is the mitigation the Sagentia IP-blocking history demands:

  1. per-domain minimum interval between requests (CRAWL_POLITE_DELAY_S)
  2. robots.txt respected (CRAWL_RESPECT_ROBOTS) — disallowed pages are skipped
     with an explicit provenance reason, never fetched anyway
  3. honest User-Agent (config.REQUEST_HEADERS)

Browser lifecycle: the Playwright sync API is not thread-safe across threads,
so each pipeline worker thread gets its own (playwright, browser, page) via
thread-local storage. Pages are reused between fetches — this removes the
~1-2 s per-page Chromium launch cost of the old one-shot _render_page_html
path. Browsers are closed at interpreter exit.
"""

import atexit
import threading
import time
from urllib import robotparser
from urllib.parse import urlparse

from config import (
    CRAWL_POLITE_DELAY_S,
    CRAWL_RESPECT_ROBOTS,
    REQUEST_HEADERS,
)

_USER_AGENT = REQUEST_HEADERS.get("User-Agent", "entity-extraction-pipeline")


# ── Politeness: per-domain rate limit ─────────────────────────────────────────

_domain_lock = threading.Lock()
_last_request_at: dict[str, float] = {}


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def wait_for_domain_slot(url: str, delay_s: float = None) -> float:
    """Block until at least delay_s has passed since the last request to this
    domain (across all threads). Returns the seconds actually slept."""
    delay = CRAWL_POLITE_DELAY_S if delay_s is None else delay_s
    if delay <= 0:
        return 0.0
    dom = _domain(url)
    while True:
        with _domain_lock:
            now = time.monotonic()
            last = _last_request_at.get(dom)
            if last is None or now - last >= delay:
                _last_request_at[dom] = now
                return 0.0 if last is None else max(0.0, now - last - delay)
            wait = delay - (now - last)
        time.sleep(wait)


# ── Politeness: robots.txt ────────────────────────────────────────────────────

_robots_lock = threading.Lock()
_robots_cache: dict[str, robotparser.RobotFileParser | None] = {}


def robots_allows(url: str) -> bool:
    """True if robots.txt permits fetching url (or robots.txt is absent or
    unreadable — unreachable robots is treated as allow, the conventional
    interpretation). Cached per domain."""
    if not CRAWL_RESPECT_ROBOTS:
        return True
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    with _robots_lock:
        rp = _robots_cache.get(base, "miss")
    if rp == "miss":
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None  # unreadable -> allow
        with _robots_lock:
            _robots_cache[base] = rp
    if rp is None:
        return True
    try:
        return rp.can_fetch(_USER_AGENT, url)
    except Exception:
        return True


# ── Thread-local browser pool ─────────────────────────────────────────────────

_tls = threading.local()
_all_pools_lock = threading.Lock()
_all_pools: list = []  # (playwright, browser) pairs for atexit cleanup


def _get_page():
    """Return this thread's reusable Playwright page, creating the browser on
    first use."""
    page = getattr(_tls, "page", None)
    if page is not None:
        return page

    from playwright.sync_api import sync_playwright  # type: ignore[import]

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(user_agent=_USER_AGENT)
    page = context.new_page()

    _tls.playwright, _tls.browser, _tls.page = pw, browser, page
    with _all_pools_lock:
        _all_pools.append((pw, browser))
    return page


def _reset_thread_browser() -> None:
    """Drop this thread's browser after a hard failure so the next fetch
    starts clean."""
    browser = getattr(_tls, "browser", None)
    pw = getattr(_tls, "playwright", None)
    for closer in (lambda: browser.close(), lambda: pw.stop()):
        try:
            closer()
        except Exception:
            pass
    with _all_pools_lock:
        if (pw, browser) in _all_pools:
            _all_pools.remove((pw, browser))
    _tls.playwright = _tls.browser = _tls.page = None


@atexit.register
def _shutdown_pools() -> None:
    with _all_pools_lock:
        pools = list(_all_pools)
        _all_pools.clear()
    for pw, browser in pools:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ── Fetch ─────────────────────────────────────────────────────────────────────

class RobotsDisallowed(Exception):
    """Raised when robots.txt forbids fetching the URL."""


def fetch_rendered_html(url: str, timeout_ms: int = 15000, settle_ms: int = 2000) -> str:
    """Politely fetch url with the pooled headless browser; return rendered HTML.

    Raises RobotsDisallowed if robots.txt forbids the URL. Other Playwright
    errors propagate after the thread's browser is reset (a crashed page would
    otherwise poison every later fetch on this thread).
    """
    if not robots_allows(url):
        raise RobotsDisallowed(url)

    wait_for_domain_slot(url)

    page = _get_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(settle_ms)
        return page.content()
    except Exception:
        _reset_thread_browser()
        raise
