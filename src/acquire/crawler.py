import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    CRAWL_MAX_DEPTH,
    CRAWL_MAX_LINKS_PER_PAGE,
)
from models import ColumnSpec, Config, PageDoc
from src.acquire.cache import read_cache, write_cache
from src.acquire.fetcher import fetch_page_with_provenance
from src.acquire.link_scorer import _tokenize, score_links
from src.acquire.models import EntityDoc, LinkCandidate


# ── Crawl planner ─────────────────────────────────────────────────────────────

_QUERY_WEIGHTS = {"question": 3.0, "entity": 2.0, "instruction": 1.0}

_FALLBACK_TOP_K = 2
_FALLBACK_MAX_DEPTH = 1


def build_crawl_query(
    columns: list[ColumnSpec],
    entities: list[str] | None = None,
) -> dict[str, float]:
    """
    Build a weighted term dict from questions, entities, and instruction text.

    Term weights: question (3.0) > entity (2.0) > instruction (1.0).
    Each term keeps its highest applicable weight.
    No hardcoded fallback terms — all signal comes from the actual task input.
    """
    tiers = {
        "question":    " ".join(col.name for col in columns),
        "entity":      " ".join(entities or []),
        "instruction": " ".join(col.instruction or "" for col in columns),
    }
    term_weights: dict[str, float] = {}
    for tier, text in tiers.items():
        w = _QUERY_WEIGHTS[tier]
        for token in _tokenize(text):
            if token not in term_weights or term_weights[token] < w:
                term_weights[token] = w
    return term_weights


def _select_links_to_follow(
    scored: list,
    min_score: float,
    depth: int,
) -> list:
    """
    Return the subset of scored LinkCandidates to follow.

    Normal: all candidates with score >= min_score.
    Fallback (depth <= _FALLBACK_MAX_DEPTH, no candidate passes): top-_FALLBACK_TOP_K only.
    """
    above = [c for c in scored if c.score >= min_score]
    if above:
        return above
    if depth <= _FALLBACK_MAX_DEPTH and scored:
        return scored[:_FALLBACK_TOP_K]
    return []


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
        return PageDoc(
            url=url, text=cached, html=None, from_cache=True,
            backend="cache", render_fallback=False, gate_passed=None, gate_reason="",
        )

    text, html, prov = fetch_page_with_provenance(url, cfg)
    write_cache(url, text, cfg.cache_dir)
    return PageDoc(
        url=url, text=text, html=html, from_cache=False,
        backend=prov["backend"], render_fallback=prov["render_fallback"],
        gate_passed=prov["gate_passed"], gate_reason=prov["gate_reason"],
    )


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
    entities: list[str] | None = None,
    diag: dict | None = None,
) -> EntityDoc:
    """
    Guided crawler.

    Scores internal links against the user-defined extraction schema,
    then selectively follows only relevant pages.
    """
    _max_depth = max_depth if max_depth is not None else CRAWL_MAX_DEPTH
    _max_pages = cfg.crawl_max_pages
    _min_score = cfg.crawl_min_score
    crawl_query = build_crawl_query(columns, entities=entities)

    visited: set[str] = set()
    selected_pages = []

    queue = deque([
        LinkCandidate(
            url=_normalise_url(start_url),
            anchor_text="start page",
            depth=0,
            score=1.0,
            parent_url=None,
        )
    ])

    while queue and len(selected_pages) < _max_pages:
        current = queue.popleft()

        if current.url in visited:
            continue

        visited.add(current.url)

        if current.depth > 0 and current.score < _min_score:
            continue

        try:
            print(f"    Acquiring page: {current.url} (depth={current.depth}, score={current.score:.2f})")
            t0 = time.time()
            page = _acquire_page_cfg(current.url, cfg)
            fetch_time_ms = int((time.time() - t0) * 1000)

            page.depth = current.depth
            page.crawl_score = current.score
            page.fetch_time_ms = fetch_time_ms

            selected_pages.append(page)

            if diag is not None:
                _page_status = (
                    "gate_failed" if page.gate_passed is False
                    else ("ok" if page.text else "empty")
                )
                diag.setdefault("acquire_log", []).append({
                    "entity_url": start_url,
                    "page_url": page.url,
                    "parent_url": current.parent_url,
                    "depth": current.depth,
                    "crawl_score": round(current.score, 3),
                    "above_threshold": current.depth == 0 or current.score >= _min_score,
                    "fetch_tool": cfg.acquire_tool,
                    "page_length": len(page.text),
                    "fetch_time_ms": fetch_time_ms,
                    "from_cache": page.from_cache,
                    "status": _page_status,
                    "skip_reason": "",
                    "backend": page.backend,
                    "render_fallback": page.render_fallback,
                    "gate_passed": page.gate_passed,
                    "gate_reason": page.gate_reason,
                })

        except Exception as e:
            print(f"    ✗ Failed to acquire {current.url}: {e}")
            if diag is not None:
                diag.setdefault("acquire_log", []).append({
                    "entity_url": start_url,
                    "page_url": current.url,
                    "parent_url": current.parent_url,
                    "depth": current.depth,
                    "crawl_score": round(current.score, 3),
                    "above_threshold": True,
                    "fetch_tool": cfg.acquire_tool,
                    "page_length": 0,
                    "fetch_time_ms": 0,
                    "from_cache": False,
                    "status": "error",
                    "skip_reason": str(e),
                    "backend": cfg.acquire_tool,
                    "render_fallback": False,
                    "gate_passed": None,
                    "gate_reason": "",
                })
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
        scored_children = score_links(unvisited, crawl_query)
        scored_children.sort(key=lambda c: c.score, reverse=True)

        to_follow = set(id(c) for c in _select_links_to_follow(scored_children, _min_score, current.depth))

        for child in scored_children:
            followed = id(child) in to_follow
            if diag is not None:
                diag.setdefault("crawl_candidates", []).append({
                    "parent_url": current.url,
                    "candidate_url": child.url,
                    "anchor_text": child.anchor_text,
                    "url_path": urlparse(child.url).path,
                    "crawl_score": round(child.score, 3),
                    "threshold": _min_score,
                    "followed": followed,
                    "skip_reason": "" if followed else "below_threshold",
                })
            if followed:
                child.parent_url = current.url
                queue.append(child)

    return EntityDoc(start_url=start_url, pages=selected_pages)
