import re
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    CRAWL_FALLBACK_TERMS,
    CRAWL_MAX_DEPTH,
    CRAWL_MAX_LINKS_PER_PAGE,
    CRAWL_MAX_PAGES,
    CRAWL_MIN_SCORE,
)
from models import ColumnSpec, Config, PageDoc
from src.acquire.cache import read_cache, write_cache
from src.acquire.fetcher import fetch_page_raw
from src.acquire.link_scorer import score_links
from src.acquire.models import EntityDoc, LinkCandidate


# ── Crawl planner ─────────────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "only",
    "return", "give", "show", "find", "extract", "information", "data",
    "value", "values", "list", "item", "items", "field", "fields",
    "about", "page", "website", "webpage",
}


def build_crawl_terms(columns: list[ColumnSpec]) -> list[str]:
    """Build crawl intent from user-defined extraction columns."""
    schema_text = " ".join(
        f"{col.name} {col.instruction or ''}"
        for col in columns
    ).lower()

    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", schema_text)

    terms = set(CRAWL_FALLBACK_TERMS)

    for token in tokens:
        token = token.lower().replace("-", " ")
        for part in token.split():
            if len(part) > 3 and part not in _STOPWORDS:
                terms.add(part)

    return sorted(terms)


# ── URL helpers ───────────────────────────────────────────────────────────────

def _normalise_url(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def _same_domain(start_url: str, candidate_url: str) -> bool:
    start_domain = urlparse(start_url).netloc.replace("www.", "")
    candidate_domain = urlparse(candidate_url).netloc.replace("www.", "")
    return start_domain == candidate_domain


# ── Page fetcher ──────────────────────────────────────────────────────────────

def _acquire_page_cfg(url: str, cfg: Config) -> PageDoc:
    cached = read_cache(url, cfg.cache_dir)
    if cached is not None:
        return PageDoc(url=url, text=cached, html=None, from_cache=True)

    text, html = fetch_page_raw(url, cfg)
    write_cache(url, text, cfg.cache_dir)
    return PageDoc(url=url, text=text, html=html, from_cache=False)


# ── Link discovery ────────────────────────────────────────────────────────────

def _discover_links(
    page_url: str,
    start_url: str,
    depth: int,
    html: str | None = None,
    cfg: Config | None = None,
) -> list[LinkCandidate]:
    if html is None:
        timeout = cfg.request_timeout if cfg else 30
        headers = cfg.request_headers if cfg else {"User-Agent": "Mozilla/5.0 guided-entity-crawler"}
        try:
            response = requests.get(page_url, timeout=timeout, headers=headers)
            response.raise_for_status()
        except Exception as e:
            print(f"    ✗ Could not discover links from {page_url}: {e}")
            return []
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        absolute_url = _normalise_url(urljoin(page_url, a["href"]))

        if not absolute_url.startswith("http"):
            continue

        if not _same_domain(start_url, absolute_url):
            continue

        anchor_text = a.get_text(" ", strip=True)
        candidates.append(
            LinkCandidate(url=absolute_url, anchor_text=anchor_text, depth=depth)
        )

    return candidates[:CRAWL_MAX_LINKS_PER_PAGE]


# ── Guided crawl ─────────────────────────────────────────────────────────────

def crawl_entity(
    start_url: str,
    columns: list[ColumnSpec],
    cfg: Config,
    max_depth: int | None = None,
) -> EntityDoc:
    """
    Guided crawler.

    Scores internal links against the user-defined extraction schema,
    then selectively follows only relevant pages.
    """
    _max_depth = max_depth if max_depth is not None else CRAWL_MAX_DEPTH
    crawl_terms = build_crawl_terms(columns)

    visited: set[str] = set()
    selected_pages = []

    queue = deque([
        LinkCandidate(
            url=_normalise_url(start_url),
            anchor_text="start page",
            depth=0,
            score=1.0,
        )
    ])

    while queue and len(selected_pages) < CRAWL_MAX_PAGES:
        current = queue.popleft()

        if current.url in visited:
            continue

        visited.add(current.url)

        if current.depth > 0 and current.score < CRAWL_MIN_SCORE:
            continue

        try:
            print(f"    Acquiring page: {current.url} (depth={current.depth}, score={current.score:.2f})")
            page = _acquire_page_cfg(current.url, cfg)
            selected_pages.append(page)
        except Exception as e:
            print(f"    ✗ Failed to acquire {current.url}: {e}")
            continue

        if current.depth >= _max_depth:
            continue

        child_links = _discover_links(
            page_url=current.url,
            start_url=start_url,
            depth=current.depth + 1,
            html=page.html,
            cfg=cfg,
        )

        unvisited = [c for c in child_links if c.url not in visited]
        scored_children = score_links(unvisited, crawl_terms)
        scored_children.sort(key=lambda c: c.score, reverse=True)

        for child in scored_children:
            if child.score >= CRAWL_MIN_SCORE:
                queue.append(child)

    return EntityDoc(start_url=start_url, pages=selected_pages)
