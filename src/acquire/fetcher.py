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
    THIN_CONTENT_FALLBACK,
)
from models import Config


class FetchProvenance(TypedDict):
    """Provenance record returned alongside fetched content."""
    backend: str           # "local_static" | "local_render" | "firecrawl" | "sgai" | "requests" | "playwright" | "playwright_pooled" | "pooled_hybrid_static" | "pooled_hybrid_render"
    render_fallback: bool  # True when Playwright was used as the local-backend fallback
    gate_passed: bool | None  # None = gate was not run (cached page or non-local backend)
    gate_reason: str       # empty when passed or not run; failure reason when gate_passed=False


# Consent-manager (CMP) overlay containers stripped from fetched HTML before
# text extraction and gating. Rendered DOMs — and some static pages — carry the
# CMP dialog as the most paragraph-like text block, so Trafilatura extracts the
# cookie policy INSTEAD of the page content. Quantified on the 2026-07-10
# hybrid bake-off: 4 Bruker pages returned an identical 2,539-char OneTrust
# modal; 5 Hologic pages PASSED the gate with 651 chars of TrustArc text
# (silent junk reaching extraction). Vendor container IDs only — generic and
# deterministic, no site-specific rules.
_CONSENT_OVERLAY_SELECTORS = [
    "#onetrust-consent-sdk", "#onetrust-banner-sdk", "#onetrust-pc-sdk",   # OneTrust
    "#CybotCookiebotDialog",                                               # Cookiebot
    "#usercentrics-root", "#usercentrics-cmp-ui",                          # Usercentrics
    "#didomi-host", "#didomi-popup",                                       # Didomi
    ".qc-cmp2-container",                                                  # Quantcast
    "#truste-consent-track", ".truste_box_overlay",                        # TrustArc
    ".osano-cm-window",                                                    # Osano
    ".cky-consent-container",                                              # CookieYes
    "#cmplz-cookiebanner-container",                                       # Complianz
    "#BorlabsCookieBox",                                                   # Borlabs
    "#iubenda-cs-banner",                                                  # iubenda
]

# Cheap substring pre-check so pages without any CMP skip the BS4 parse.
_CONSENT_FINGERPRINTS = (
    "onetrust", "cookiebot", "usercentrics", "didomi", "qc-cmp2",
    "truste", "osano", "cky-consent", "cmplz", "borlabscookie", "iubenda",
)


def _strip_consent_overlays(html: str) -> str:
    """Remove known consent-manager containers from html; unchanged when none found."""
    low = html.lower()
    if not any(fp in low for fp in _CONSENT_FINGERPRINTS):
        return html
    soup = BeautifulSoup(html, "html.parser")
    found = False
    for sel in _CONSENT_OVERLAY_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
            found = True
    return str(soup) if found else html


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


def _thin_content_gate(text: str) -> tuple[bool, str]:
    """Minimum-character check for non-local backends (no HTML available for full gate)."""
    content_chars = len(text.strip())
    if content_chars < QUALITY_MIN_CHARS:
        return False, f"thin_content_{content_chars}_chars"
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


def _fetch_firecrawl_doc(url: str, cfg: Config) -> tuple[str, str | None]:
    """Fetch markdown + raw rendered HTML in a single Firecrawl call.

    HTML is used for link discovery only (crawler._discover_links). Firecrawl drops
    some nav/footer links (About/Contact) from BOTH its markdown AND its cleaned `html`
    output — verified on www.surmodics.com: /about-surmodics is absent from markdown
    (11 KB) and result.html (2.0 MB) but present in result.raw_html (3.0 MB). So we
    request the "rawHtml" format and read the snake_case attribute result.raw_html.
    _fetch_firecrawl (markdown-only, str) is left intact for _FETCHERS / fetch_page_raw.
    """
    from firecrawl import FirecrawlApp  # type: ignore[import]

    app = FirecrawlApp(api_key=cfg.firecrawl_api_key)
    result = app.scrape_url(url, formats=["markdown", "rawHtml"])
    return (result.markdown or ""), (getattr(result, "raw_html", None) or None)


def _fetch_firecrawl_with_fallback(url: str, cfg: Config) -> tuple[str, str | None, FetchProvenance]:
    """Fetch via Firecrawl; if thin, attempt one Playwright re-render (when enabled)."""
    text, html = _fetch_firecrawl_doc(url, cfg)
    gate_passed, gate_reason = _thin_content_gate(text)

    if gate_passed:
        return text, html, FetchProvenance(
            backend="firecrawl", render_fallback=False, gate_passed=True, gate_reason="",
        )

    if not THIN_CONTENT_FALLBACK:
        return text, None, FetchProvenance(
            backend="firecrawl", render_fallback=False, gate_passed=False, gate_reason=gate_reason,
        )

    print(f"    [thin-content] {gate_reason} — re-rendering with Playwright")
    try:
        pw_text = _fetch_playwright(url, cfg)
        if len(pw_text.strip()) >= len(text.strip()):
            pw_passed, pw_reason = _thin_content_gate(pw_text)
            combined = f"{gate_reason}; playwright_fallback: {pw_reason or 'ok'}"
            return pw_text, None, FetchProvenance(
                backend="firecrawl", render_fallback=True, gate_passed=pw_passed, gate_reason=combined,
            )
        combined = f"{gate_reason}; playwright_also_thin_{len(pw_text.strip())}_chars"
        return text, None, FetchProvenance(
            backend="firecrawl", render_fallback=True, gate_passed=False, gate_reason=combined,
        )
    except Exception as e:
        return text, None, FetchProvenance(
            backend="firecrawl", render_fallback=False, gate_passed=False,
            gate_reason=f"{gate_reason}; playwright_error={e}",
        )


def _fetch_playwright_pooled(url: str, cfg: Config) -> tuple[str, str, FetchProvenance]:
    """
    Self-hosted render-first backend: pooled headless Chromium (one persistent
    browser per worker thread) -> Trafilatura text -> full quality gate.
    Returns (text, rendered_html, provenance); the rendered DOM feeds link
    discovery, so the nav/footer links Firecrawl's outputs drop are present
    by construction (no rawHtml workaround needed).

    Politeness (per-domain delay, robots.txt, honest UA) is enforced inside
    src/acquire/playwright_pool.py — requests come from this machine's IP.
    """
    from src.acquire.playwright_pool import RobotsDisallowed, fetch_rendered_html

    try:
        html = fetch_rendered_html(url)
    except RobotsDisallowed:
        return "", "", FetchProvenance(
            backend="playwright_pooled", render_fallback=False,
            gate_passed=False, gate_reason="robots_disallowed",
        )

    html = _strip_consent_overlays(html)
    text = _extract_text_from_html(html)
    gate_passed, gate_reason = content_quality_gate(text, html)
    return text, html, FetchProvenance(
        backend="playwright_pooled", render_fallback=False,
        gate_passed=gate_passed, gate_reason=gate_reason,
    )


def _fetch_playwright_pooled_hybrid(url: str, cfg: Config) -> tuple[str, str, FetchProvenance]:
    """
    Static-first variant of playwright_pooled: httpx GET -> Trafilatura -> full
    quality gate, escalating to the pooled browser render only when the static
    attempt fails the gate (or errors). Most pages don't need JavaScript, so
    the browser (and its settle wait) is skipped wherever static HTML passes.

    Politeness is identical to playwright_pooled: the SAME robots_allows /
    wait_for_domain_slot primitives guard the static request, and the render
    escalation goes through fetch_rendered_html, which takes its own domain
    slot — correct, since the escalation is a second request to the domain.

    Provenance records which path produced the content:
    "pooled_hybrid_static" | "pooled_hybrid_render" (render_fallback=True).
    """
    from src.acquire.playwright_pool import (
        RobotsDisallowed,
        fetch_rendered_html,
        robots_allows,
        wait_for_domain_slot,
    )

    if not robots_allows(url):
        return "", "", FetchProvenance(
            backend="pooled_hybrid_static", render_fallback=False,
            gate_passed=False, gate_reason="robots_disallowed",
        )

    static_text, static_html = "", ""
    try:
        wait_for_domain_slot(url)
        r = httpx.get(
            url,
            headers=cfg.request_headers,
            timeout=cfg.request_timeout,
            follow_redirects=True,
        )
        r.raise_for_status()
        static_html = _strip_consent_overlays(r.text)
        static_text = _extract_text_from_html(static_html)
        gate_passed, gate_reason = content_quality_gate(static_text, static_html)
        if gate_passed:
            return static_text, static_html, FetchProvenance(
                backend="pooled_hybrid_static", render_fallback=False,
                gate_passed=True, gate_reason="",
            )
    except Exception as e:
        gate_reason = f"static_error={e}"

    print(f"    [hybrid-gate] {gate_reason} — escalating to pooled render")
    try:
        html = fetch_rendered_html(url)
    except RobotsDisallowed:
        # Defensive only: robots was already checked (and is cached) above.
        return "", "", FetchProvenance(
            backend="pooled_hybrid_render", render_fallback=True,
            gate_passed=False, gate_reason="robots_disallowed",
        )
    except Exception as e:
        if static_html:
            # Keep the gate-failed static content rather than losing it; the
            # failure stays recorded — same contract as the local backend.
            return static_text, static_html, FetchProvenance(
                backend="pooled_hybrid_static", render_fallback=False,
                gate_passed=False, gate_reason=f"{gate_reason}; render_error={e}",
            )
        raise

    html = _strip_consent_overlays(html)
    text = _extract_text_from_html(html)
    gate_passed, gate_reason = content_quality_gate(text, html)
    return text, html, FetchProvenance(
        backend="pooled_hybrid_render", render_fallback=True,
        gate_passed=gate_passed, gate_reason=gate_reason,
    )


def _render_page_html(url: str, cfg: Config) -> str:
    """Launch Playwright and return raw rendered HTML (no text extraction)."""
    from playwright.sync_api import sync_playwright  # type: ignore[import]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        html = page.content()
        browser.close()
    return html


def _fetch_playwright(url: str, cfg: Config) -> str:
    return _html_to_text(_strip_consent_overlays(_render_page_html(url, cfg)))


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
    static_html = _strip_consent_overlays(r.text)

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
        render_html = _strip_consent_overlays(_render_page_html(url, cfg))
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
_VALID_BACKENDS = set(_FETCHERS) | {"local", "playwright_pooled", "playwright_pooled_hybrid"}


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
    if cfg.acquire_tool == "playwright_pooled":
        text, html, _ = _fetch_playwright_pooled(url, cfg)
        return text, html
    if cfg.acquire_tool == "playwright_pooled_hybrid":
        text, html, _ = _fetch_playwright_pooled_hybrid(url, cfg)
        return text, html
    return _FETCHERS[cfg.acquire_tool](url, cfg), None


def fetch_page_with_provenance(url: str, cfg: Config) -> tuple[str, str | None, FetchProvenance]:
    """
    Unified fetch entry point returning (text, html_or_None, provenance).

    The 'local' backend runs the full three-rule quality gate and Playwright fallback.
    All other backends run a minimum-character thin-content check; Firecrawl also
    falls back to Playwright on thin content when THIN_CONTENT_FALLBACK is enabled.
    """
    if cfg.acquire_tool == "local":
        return _fetch_local(url, cfg)

    if cfg.acquire_tool == "playwright_pooled":
        return _fetch_playwright_pooled(url, cfg)

    if cfg.acquire_tool == "playwright_pooled_hybrid":
        return _fetch_playwright_pooled_hybrid(url, cfg)

    if cfg.acquire_tool == "requests":
        response = requests.get(url, timeout=cfg.request_timeout, headers=cfg.request_headers)
        response.raise_for_status()
        html = response.text
        text = _html_to_text(html)
        gate_passed, gate_reason = _thin_content_gate(text)
        return text, html, FetchProvenance(
            backend="requests", render_fallback=False,
            gate_passed=gate_passed, gate_reason=gate_reason,
        )

    if cfg.acquire_tool not in _FETCHERS:
        raise ValueError(
            f"Unknown acquire_tool: {cfg.acquire_tool!r}. "
            f"Choose from: {sorted(_VALID_BACKENDS)}"
        )

    if cfg.acquire_tool == "firecrawl":
        return _fetch_firecrawl_with_fallback(url, cfg)

    text = _FETCHERS[cfg.acquire_tool](url, cfg)
    gate_passed, gate_reason = _thin_content_gate(text)
    return text, None, FetchProvenance(
        backend=cfg.acquire_tool, render_fallback=False,
        gate_passed=gate_passed, gate_reason=gate_reason,
    )
