import os
import hashlib
import requests
from bs4 import BeautifulSoup

from config import CACHE_DIR, REQUEST_HEADERS
from models import Config, FetchedPage, PageDoc


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(url: str, cache_dir: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{key}.txt")


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Fetcher backends  (each returns the page content as a string)
# ---------------------------------------------------------------------------

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


def _fetch_playwright(url: str, cfg: Config) -> str:
    from playwright.sync_api import sync_playwright  # type: ignore[import]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=cfg.request_timeout * 1000)
        html = page.content()
        browser.close()
    return _html_to_text(html)


_FETCHERS = {
    "requests": _fetch_requests,
    "sgai": _fetch_sgai,
    "firecrawl": _fetch_firecrawl,
    "playwright": _fetch_playwright,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def acquire(urls: list[str], cfg: Config) -> list[FetchedPage]:
    """Fetch each URL exactly once (sha256-keyed disk cache) and return FetchedPages."""
    fetcher = _FETCHERS.get(cfg.acquire_tool)
    if fetcher is None:
        raise ValueError(f"Unknown acquire_tool: {cfg.acquire_tool!r}. Choose from: {list(_FETCHERS)}")

    results: list[FetchedPage] = []

    for url in urls:
        cache_file = _cache_path(url, cfg.cache_dir)

        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                markdown = f.read()
            results.append(FetchedPage(url=url, parent_url=None, markdown=markdown, status="cached"))
            continue

        try:
            markdown = fetcher(url, cfg)
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(markdown)
            results.append(FetchedPage(url=url, parent_url=None, markdown=markdown, status="ok"))
        except Exception as e:
            print(f"    [FAIL] acquire {url}: {e}")
            results.append(FetchedPage(url=url, parent_url=None, markdown="", status="error"))

    return results


# ---------------------------------------------------------------------------
# Backward-compat shim — used by pipeline.py and crawler.py
# ---------------------------------------------------------------------------

def acquire_page(url: str) -> PageDoc:
    cache_file = _cache_path(url, CACHE_DIR)

    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            text = f.read()
        return PageDoc(url=url, text=text, html=None, from_cache=True)

    response = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
    response.raise_for_status()

    html = response.text
    text = _html_to_text(html)

    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(text)

    return PageDoc(url=url, text=text, html=html, from_cache=False)
