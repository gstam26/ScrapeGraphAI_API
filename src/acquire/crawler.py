import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    CRAWL_LOCALE_DEDUP,
    CRAWL_MAX_DEPTH,
    CRAWL_MAX_LINKS_PER_PAGE,
    CRAWL_MIN_SCORE_EMBED,
    SCORER_TOOL,
)
from models import ColumnSpec, Config, PageDoc
from src.acquire.cache import read_cache, write_cache
from src.acquire.fetcher import fetch_page_with_provenance
from src.acquire.link_scorer import (
    _tokenize,
    score_links,
    score_links_embed,
    score_links_embed_experimental,
)
from src.acquire.acquire_models import EntityDoc, LinkCandidate


# ── Crawl planner ─────────────────────────────────────────────────────────────

_QUERY_WEIGHTS = {"question": 3.0, "instruction": 1.0}

_FALLBACK_TOP_K = 2
_FALLBACK_MAX_DEPTH = 1


def build_crawl_query(
    columns: list[ColumnSpec],
    entities: list[str] | None = None,
) -> dict[str, float]:
    """
    Build a weighted term dict from questions and instruction text (BM25 scorer).

    Entity names are intentionally excluded — they dilute relevance scoring
    and belong in link hygiene filtering (_same_domain) instead.
    Term weights: question (3.0) > instruction (1.0).
    """
    tiers = {
        "question":    " ".join(col.name for col in columns),
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


_JUNK_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".mp4", ".webm", ".pdf", ".zip",
)


def _is_junk_link(url: str) -> bool:
    return urlparse(url).path.lower().rstrip("/").endswith(_JUNK_EXTS)


# Path segments that are pure locale/language codes: "fr", "en-us", "ko_kr".
_LOCALE_SEG_RE = re.compile(r"^[a-z]{2}([_-][a-z]{2})?$")


def _locale_key(url: str) -> str:
    """Collapse locale path segments so language variants of one page share a key.

    bruker.com/fr.html and /de.html -> same key (translated homepages);
    quidelortho.com/de/de and /jp/ja -> same key;
    aladdinsci.com/us_en/contact and /us_en/products -> DIFFERENT keys (the
    locale segment is normalised, the content segments still distinguish them).
    The query string is kept so ?id=... product pages are never collapsed.
    Known trade-off: a genuine 2-letter content segment (e.g. /it/ meaning
    "information technology") is treated as a locale — first variant is kept.
    """
    parsed = urlparse(url)
    segments = []
    for seg in parsed.path.split("/"):
        stem = seg[:-5] if seg.endswith(".html") else seg
        if seg and _LOCALE_SEG_RE.match(stem.lower()):
            segments.append("{locale}.html" if seg.endswith(".html") else "{locale}")
        else:
            segments.append(seg)
    key = f"{parsed.netloc.replace('www.', '')}{'/'.join(segments)}"
    if parsed.query:
        key += f"?{parsed.query}"
    return key


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

_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)\s"]+)(?:\s+"[^"]*")?\)')


def _discover_links_from_markdown(
    page_url: str,
    start_url: str,
    depth: int,
    text: str,
) -> list[LinkCandidate]:
    """Extract links with ±120-char context from Firecrawl markdown."""
    seen: set[str] = set()
    candidates = []

    for m in _MD_LINK_RE.finditer(text):
        anchor = m.group(1).strip()
        raw_url = m.group(2).strip()

        if raw_url.startswith(("mailto:", "javascript:", "#", "tel:", "data:")):
            continue

        absolute_url = _normalise_url(urljoin(page_url, raw_url))
        if not absolute_url.startswith("http"):
            continue
        if not _same_domain(start_url, absolute_url):
            continue
        if _is_junk_link(absolute_url):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)

        cs = max(0, m.start() - 120)
        ce = min(len(text), m.end() + 120)
        raw = text[cs:ce].replace("\n", " ")
        rs, re_ = m.start() - cs, m.end() - cs
        ctx = (raw[:rs].rstrip() + " " + raw[re_:].lstrip()).strip()

        candidates.append(
            LinkCandidate(url=absolute_url, anchor_text=anchor, depth=depth, context=ctx)
        )

    # No truncation here: the CRAWL_MAX_LINKS_PER_PAGE cap is applied in
    # crawl_entity AFTER scoring (top-N by score, not first-N in DOM order).
    return candidates


def _discover_links_from_html(
    page_url: str,
    start_url: str,
    depth: int,
    html: str,
) -> list[LinkCandidate]:
    """Extract links from HTML; context comes from the parent element text."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    candidates = []

    for a in soup.find_all("a", href=True):
        absolute_url = _normalise_url(urljoin(page_url, a["href"]))
        if not absolute_url.startswith("http"):
            continue
        if not _same_domain(start_url, absolute_url):
            continue
        if _is_junk_link(absolute_url):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)

        anchor_text = a.get_text(" ", strip=True)
        parent = a.parent
        ctx = parent.get_text(" ", strip=True)[:240] if parent else ""

        candidates.append(
            LinkCandidate(url=absolute_url, anchor_text=anchor_text, depth=depth, context=ctx)
        )

    # No truncation here: the CRAWL_MAX_LINKS_PER_PAGE cap is applied in
    # crawl_entity AFTER scoring (top-N by score, not first-N in DOM order).
    return candidates


def _discover_links(
    page_url: str,
    start_url: str,
    depth: int,
    html: str | None = None,
    page_text: str | None = None,
    cfg: Config | None = None,
) -> list[LinkCandidate]:
    # Firecrawl + playwright_pooled: prefer rendered HTML. Firecrawl drops some
    # nav/footer links (About/Contact) from both its markdown and cleaned html;
    # only raw_html keeps them. playwright_pooled hands us the real rendered DOM,
    # which has them by construction. This re-enables the parent-element
    # ("nav-soup") context the 2026-06-16 decision moved away from — scoped to
    # these two backends; the local backend keeps its markdown path
    # (Trafilatura include_links=True) and its ±120-char prose context.
    if html and cfg is not None and cfg.acquire_tool in ("firecrawl", "playwright_pooled"):
        return _discover_links_from_html(page_url, start_url, depth, html)

    # Markdown path: Firecrawl cache hits / local backend — context comes naturally.
    if page_text and "](" in page_text:
        return _discover_links_from_markdown(page_url, start_url, depth, page_text)

    # No usable HTML or markdown links: fetch HTML ourselves (unchanged fallback).
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

    return _discover_links_from_html(page_url, start_url, depth, html)


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
    _min_score = (
        cfg.crawl_min_score_embed if SCORER_TOOL == "ollama"
        else cfg.crawl_min_score
    )
    crawl_scorer = (cfg.crawl_scorer or "baseline").strip().lower()
    if crawl_scorer not in {"baseline", "experimental"}:
        raise ValueError("crawl_scorer must be 'baseline' or 'experimental'")
    crawl_query = build_crawl_query(columns, entities=entities)

    visited: set[str] = set()
    # Locale keys of pages already fetched (or queued for fetch): a candidate
    # whose key matches is a translated copy of a page we already have.
    visited_locale_keys: set[str] = set()
    if CRAWL_LOCALE_DEDUP:
        visited_locale_keys.add(_locale_key(_normalise_url(start_url)))
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
                    "crawl_scorer": crawl_scorer,
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
                    "crawl_scorer": crawl_scorer,
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
            page_text=page.text,
            cfg=cfg,
        )

        unvisited = []
        batch_locale_keys: set[str] = set()
        for c in child_links:
            if c.url in visited:
                continue
            if CRAWL_LOCALE_DEDUP:
                key = _locale_key(c.url)
                # Drop translated copies of pages already fetched, and keep
                # only one locale variant within this batch (first in DOM
                # order — variants carry identical content either way).
                if key in visited_locale_keys or key in batch_locale_keys:
                    continue
                batch_locale_keys.add(key)
            unvisited.append(c)

        if crawl_scorer == "experimental":
            try:
                scored_children = score_links_embed_experimental(unvisited, columns)
            except Exception as exc:
                print(f"    ! Experimental scorer failed ({exc}); falling back to baseline")
                if SCORER_TOOL == "ollama":
                    try:
                        questions = [col.name for col in columns]
                        scored_children = score_links_embed(unvisited, questions)
                    except Exception as embed_exc:
                        print(f"    ! Ollama scorer failed ({embed_exc}); falling back to BM25")
                        scored_children = score_links(unvisited, crawl_query)
                        scored_children.sort(key=lambda c: c.score, reverse=True)
                else:
                    scored_children = score_links(unvisited, crawl_query)
                    scored_children.sort(key=lambda c: c.score, reverse=True)
        elif SCORER_TOOL == "ollama":
            try:
                questions = [col.name for col in columns]
                scored_children = score_links_embed(unvisited, questions)
            except Exception as exc:
                print(f"    ! Ollama scorer failed ({exc}); falling back to BM25")
                scored_children = score_links(unvisited, crawl_query)
                scored_children.sort(key=lambda c: c.score, reverse=True)
        else:
            scored_children = score_links(unvisited, crawl_query)
            scored_children.sort(key=lambda c: c.score, reverse=True)

        # Score-aware cap: every scorer path returns candidates sorted
        # best-first, so this keeps the top-N by relevance. Previously the cap
        # was a DOM-order slice inside the discovery functions, which dropped
        # footer About/locations links before the scorer ever saw them.
        scored_children = scored_children[:CRAWL_MAX_LINKS_PER_PAGE]

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
                    "crawl_scorer": crawl_scorer,
                    "threshold": _min_score,
                    "followed": followed,
                    "skip_reason": "" if followed else "below_threshold",
                })
            if followed:
                child.parent_url = current.url
                if CRAWL_LOCALE_DEDUP:
                    # Claim the key at queue time so a variant discovered on a
                    # later page can't also be queued before this one fetches.
                    visited_locale_keys.add(_locale_key(child.url))
                queue.append(child)

    return EntityDoc(start_url=start_url, pages=selected_pages)
