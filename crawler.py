from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from acquire import acquire_page
from config import (
    CRAWL_MAX_DEPTH,
    CRAWL_MAX_PAGES,
    CRAWL_MIN_SCORE,
    CRAWL_MAX_LINKS_PER_PAGE,
)
from crawl_planner import build_crawl_terms
from link_scorer import score_link
from models import ColumnSpec, EntityDoc, LinkCandidate


def _normalise_url(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def _same_domain(start_url: str, candidate_url: str) -> bool:
    start_domain = urlparse(start_url).netloc.replace("www.", "")
    candidate_domain = urlparse(candidate_url).netloc.replace("www.", "")
    return start_domain == candidate_domain


def _discover_links(page_url: str, start_url: str, depth: int) -> list[LinkCandidate]:
    try:
        response = requests.get(
            page_url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 guided-entity-crawler"},
        )
        response.raise_for_status()
    except Exception as e:
        print(f"    ✗ Could not discover links from {page_url}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        absolute_url = _normalise_url(urljoin(page_url, a["href"]))

        if not absolute_url.startswith("http"):
            continue

        if not _same_domain(start_url, absolute_url):
            continue

        anchor_text = a.get_text(" ", strip=True)

        candidates.append(
            LinkCandidate(
                url=absolute_url,
                anchor_text=anchor_text,
                depth=depth,
            )
        )

    return candidates[:CRAWL_MAX_LINKS_PER_PAGE]


def crawl_entity(start_url: str, columns: list[ColumnSpec]) -> EntityDoc:
    """
    Guided crawler.

    It does not blindly crawl a website.
    It scores internal links against the user-defined extraction schema,
    then selectively goes deeper only through relevant pages.
    """

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

        if current.depth > 0:
            current = score_link(current, crawl_terms)

            if current.score < CRAWL_MIN_SCORE:
                continue

        try:
            print(f"    Acquiring page: {current.url} (depth={current.depth}, score={current.score:.2f})")
            page = acquire_page(current.url)
            selected_pages.append(page)
        except Exception as e:
            print(f"    ✗ Failed to acquire {current.url}: {e}")
            continue

        if current.depth >= CRAWL_MAX_DEPTH:
            continue

        child_links = _discover_links(
            page_url=current.url,
            start_url=start_url,
            depth=current.depth + 1,
        )

        scored_children = [
            score_link(child, crawl_terms)
            for child in child_links
            if child.url not in visited
        ]

        scored_children = sorted(
            scored_children,
            key=lambda link: link.score,
            reverse=True,
        )

        for child in scored_children:
            if child.score >= CRAWL_MIN_SCORE:
                queue.append(child)

    return EntityDoc(
        start_url=start_url,
        pages=selected_pages,
    )