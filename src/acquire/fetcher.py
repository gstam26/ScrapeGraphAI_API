import os

import requests
from bs4 import BeautifulSoup

from models import Config


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


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
