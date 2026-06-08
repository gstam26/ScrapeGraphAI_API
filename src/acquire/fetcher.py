import os
from typing import TypedDict

import httpx
import trafilatura
import requests
from bs4 import BeautifulSoup

from config import (
    QUALITY_MIN_CHARS,
    QUALITY_MAX_LINK_DENSITY,
    QUALITY_MIN_CONTENT_RATIO,
)
from models import Config


class FetchProvenance(TypedDict):
    """Provenance record returned alongside fetched content."""
    backend: str           # "local_static" | "local_render" | "firecrawl" | "sgai" | "requests" | "playwright"
    render_fallback: bool  # True when Playwright was used as the local-backend fallback
    gate_passed: bool | None  # None = gate was not run (cached page or non-local backend)
    gate_reason: str       # empty when passed or not run; failure reason when gate_passed=False


def _html_to_text(html: str) -> str:
    """BeautifulSoup plain-text extraction (script/style stripped)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_text_from_html(html: str) -> str:
    """Trafilatura semantic extraction, with BS4 get_text() as fallback."""
    text = trafilatura.extract(
        html, include_links=False, include_tables=True, no_fallback=False
    ) or ""
    if not text:
        text = _html_to_text(html)
    return text


def content_quality_gate(text: str, html: str) -> tuple[bool, str]:
    """
    Explicit content quality check applied after local-backend extraction.
    Returns (passed, reason); reason is an empty string when the gate passes.

    Three named rules — all must pass:

      1. MIN_CHARS  : extracted chars >= QUALITY_MIN_CHARS
         Nav/footer-only pages rarely produce substantial text after stripping.
         This is the primary guard against the Table 4.1 silent-junk failure mode.

      2. LINK_DENSITY : anchor-text chars / body-text chars <= QUALITY_MAX_LINK_DENSITY
         A high link density signals a navigation listing or link directory,
         not article content.

      3. CONTENT_RATIO : extracted chars / full-page plain-text chars >= QUALITY_MIN_CONTENT_RATIO
         Trafilatura removes boilerplate; very low retention means it found almost
         nothing worth keeping, which usually means the page is mostly chrome.
    """
    content_chars = len(text.strip())

    # Rule 1 — minimum extracted content length
    if content_chars < QUALITY_MIN_CHARS:
        return False, (
            f"content_chars={content_chars} < QUALITY_MIN_CHARS={QUALITY_MIN_CHARS}"
        )

    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body") or soup
    for tag in body(["script", "style", "noscript"]):
        tag.decompose()

    # Non-whitespace chars in the full body (denominator for ratios)
    body_chars = max(len(body.get_text(separator=" ").replace(" ", "")), 1)

    # Rule 2 — link density
    link_chars = sum(len(a.get_text()) for a in body.find_all("a"))
    link_density = link_chars / body_chars
    if link_density > QUALITY_MAX_LINK_DENSITY:
        return False, (
            f"link_density={link_density:.2f} > QUALITY_MAX_LINK_DENSITY={QUALITY_MAX_LINK_DENSITY}"
        )

    # Rule 3 — content retention ratio
    content_ratio = content_chars / body_chars
    if content_ratio < QUALITY_MIN_CONTENT_RATIO:
        return False, (
            f"content_ratio={content_ratio:.2f} < QUALITY_MIN_CONTENT_RATIO={QUALITY_MIN_CONTENT_RATIO}"
        )

    return True, ""


def _fetch_requests(url: str, cfg: Config) -> str:
    response = requests.get(url, timeout=cfg.request_timeout, headers=cfg.request_headers)
    response.raise_for_status()
    return _html_to_text(response.text)


def _fetch_sgai(url: str, cfg: Config) -> str:
    from scrapegraph_py import ScrapeGraphAI, MarkdownFormatConfig  # type: ignore[import]

    api_key = cfg.sgai_api_key or os.getenv("SGAI_API_KEY")
    sgai = ScrapeGraphAI(api_key=api_key)
    try:
        result = sgai.scrape(url, formats=[MarkdownFormatConfig()])
        return result.data.results.get("markdown", {}).get("data", "") or ""
    finally:
        sgai.close()


def _fetch_firecrawl(url: str, cfg: Config) -> str:
    from firecrawl import FirecrawlApp  # type: ignore[import]

    app = FirecrawlApp(api_key=cfg.firecrawl_api_key)
    result = app.scrape_url(url, formats=["markdown"])
    return result.markdown or ""


def _render_page_html(url: str, cfg: Config) -> str:
    """Launch Playwright and return raw rendered HTML (no text extraction)."""
    from playwright.sync_api import sync_playwright  # type: ignore[import]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=cfg.request_timeout * 1000)
        html = page.content()
        browser.close()
    return html


def _fetch_playwright(url: str, cfg: Config) -> str:
    return _html_to_text(_render_page_html(url, cfg))


def _fetch_local(url: str, cfg: Config) -> tuple[str, str, FetchProvenance]:
    """
    httpx GET → Trafilatura extraction → quality gate → Playwright fallback if gate fails.
    Returns (text, html, provenance).

    The quality gate is run twice: once on the static HTML, once on the rendered HTML
    if the first attempt fails.  The gate result and reason are always recorded so
    a failed fetch is inspectable rather than silently returning junk.
    """
    r = httpx.get(
        url,
        headers=cfg.request_headers,
        timeout=cfg.request_timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    static_html = r.text

    text = _extract_text_from_html(static_html)
    gate_passed, gate_reason = content_quality_gate(text, static_html)

    if gate_passed:
        return text, static_html, FetchProvenance(
            backend="local_static", render_fallback=False,
            gate_passed=True, gate_reason="",
        )

    # Gate failed on static fetch — attempt Playwright re-render
    print(f"    [quality-gate] FAIL ({gate_reason}) — re-rendering with Playwright")
    try:
        render_html = _render_page_html(url, cfg)
        render_text = _extract_text_from_html(render_html)
        render_passed, render_reason = content_quality_gate(render_text, render_html)
        return render_text, render_html, FetchProvenance(
            backend="local_render", render_fallback=True,
            gate_passed=render_passed, gate_reason=render_reason,
        )
    except Exception as e:
        # Playwright failed — return original static content; gate failure stays recorded
        return text, static_html, FetchProvenance(
            backend="local_static", render_fallback=False,
            gate_passed=False, gate_reason=f"{gate_reason}; playwright_error={e}",
        )


_FETCHERS = {
    "requests": _fetch_requests,
    "sgai": _fetch_sgai,
    "firecrawl": _fetch_firecrawl,
    "playwright": _fetch_playwright,
}

# All valid acquire_tool values including the local backend
_VALID_BACKENDS = set(_FETCHERS) | {"local"}


def fetch_page_raw(url: str, cfg: Config) -> tuple[str, str | None]:
    """Fetch url; return (text, html). html is non-None only for the requests backend."""
    if cfg.acquire_tool == "requests":
        response = requests.get(url, timeout=cfg.request_timeout, headers=cfg.request_headers)
        response.raise_for_status()
        html = response.text
        return _html_to_text(html), html
    if cfg.acquire_tool == "local":
        text, html, _ = _fetch_local(url, cfg)
        return text, html
    return _FETCHERS[cfg.acquire_tool](url, cfg), None


def fetch_page_with_provenance(url: str, cfg: Config) -> tuple[str, str | None, FetchProvenance]:
    """
    Unified fetch entry point returning (text, html_or_None, provenance).

    The 'local' backend runs the quality gate and Playwright fallback.
    All other backends return trivial provenance (gate_passed=None).
    """
    if cfg.acquire_tool == "local":
        return _fetch_local(url, cfg)

    if cfg.acquire_tool == "requests":
        response = requests.get(url, timeout=cfg.request_timeout, headers=cfg.request_headers)
        response.raise_for_status()
        html = response.text
        return _html_to_text(html), html, FetchProvenance(
            backend="requests", render_fallback=False, gate_passed=None, gate_reason="",
        )

    if cfg.acquire_tool not in _FETCHERS:
        raise ValueError(
            f"Unknown acquire_tool: {cfg.acquire_tool!r}. "
            f"Choose from: {sorted(_VALID_BACKENDS)}"
        )

    text = _FETCHERS[cfg.acquire_tool](url, cfg)
    return text, None, FetchProvenance(
        backend=cfg.acquire_tool, render_fallback=False, gate_passed=None, gate_reason="",
    )
