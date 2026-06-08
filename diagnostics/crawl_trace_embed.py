"""
Crawl trace diagnostic — EMBEDDING variant.

Identical to crawl_trace.py except the scoring layer: instead of BM25 lexical
scoring, links are scored by semantic similarity using the Ollama
nomic-embed-text endpoint. Everything else (Firecrawl cache-first fetch, link
extraction, recursion, FOLLOW/SKIP trace) is unchanged, so this reads the SAME
cache files the BM25 trace wrote — letting you diff the two FOLLOW sets on
identical pages with zero re-fetch.

Purpose: answer the RQ4 question — does semantic similarity follow links that
lexical BM25 skips (e.g. an opaque "Life Cycle Assessment" anchor with no
keyword overlap)? Run both, compare the traces.

NOTE: this scorer is PURE semantic — it deliberately does NOT apply the
URL/anchor keyword boosts the BM25 version uses, so the comparison isolates
what the transformer contributes on its own. Add a hybrid later if wanted.

Usage:
    python diagnostics/crawl_trace_embed.py
    python diagnostics/crawl_trace_embed.py --dry-run

Requires:
    FIRECRAWL_API_KEY in .env
    Reachable Ollama host (only resolves on the internal network/VPN)
    pip install firecrawl-py python-dotenv
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_REPO_ROOT, "cache")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.acquire.cache import cache_path as _cache_path_fn

# ── Crawl config ──────────────────────────────────────────────────────────────

URL = "https://www.oatly.com/en-us"

MAX_DEPTH = 3
TOP_K_PER_PAGE = 3
FETCH_TIMEOUT_MS = 60_000

# Cosine distributions differ from BM25 raw scores, so this threshold is tuned
# SEPARATELY from the BM25 trace's 0.30. Both scripts normalise per-page
# (score = raw / page_max), so the 0-1 score column means "fraction of the best
# link on this page" in both — but the cut-off that separates FOLLOW from SKIP
# will not be the same number. Start here, then tune against the actual traces.
THRESHOLD = 0.60

# ── Ollama embedding config (mirrors config.py; standalone for the diagnostic) ──

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://10.99.96.1:11434")
OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_TIMEOUT = 60
OLLAMA_KEEP_ALIVE = "10m"

# nomic-embed-text asymmetric-retrieval prefixes (query vs document).
QUERY_PREFIX = "search_query: "
DOC_PREFIX = "search_document: "

# Use crawl-guidance topics here, not final extraction questions.
TOPICS = [
    "sustainability",
    "sustainability report",
    "climate",
    "environment",
    "impact",
    "responsibility",
    "emissions",
    "carbon",
    "net zero",
    "ESG",
    "planet",
    "annual report",
    "social responsibility",
    "sustainable packaging",
    "renewable energy",
]


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(url: str) -> str:
    return _cache_path_fn(url, cache_dir=CACHE_DIR, ext=".md")


def _fetch(url: str, app) -> tuple[str, bool]:
    """Return (markdown, from_cache). Writes to cache/ on live fetch."""
    path = _cache_path(url)

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read(), True

    result = app.scrape_url(url, formats=["markdown"], timeout=FETCH_TIMEOUT_MS)

    if not result.success or not result.markdown:
        raise RuntimeError(result.error or "empty response from Firecrawl")

    md = result.markdown.strip()

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)

    return md, False


# ── Link extraction (unchanged from crawl_trace.py) ─────────────────────────────

def _same_domain(base_url: str, candidate_url: str) -> bool:
    base = urlparse(base_url).netloc.replace("www.", "")
    cand = urlparse(candidate_url).netloc.replace("www.", "")
    return cand == base or cand.endswith("." + base)


def _extract_links(markdown: str, page_url: str) -> list[dict]:
    """Return unique same-domain links with anchor text and context snippet."""
    pattern = re.compile(r'\[([^\]]+)\]\(([^)\s"]+)(?:\s+"[^"]*")?\)')

    seen: set[str] = set()
    out: list[dict] = []

    for m in pattern.finditer(markdown):
        anchor = m.group(1).strip()
        raw_url = m.group(2).strip()

        if raw_url.startswith(("mailto:", "javascript:", "#", "tel:", "data:")):
            continue

        resolved = urljoin(page_url, raw_url).split("#")[0].rstrip("/")

        if not resolved.startswith("http"):
            continue

        if not _same_domain(page_url, resolved):
            continue

        if resolved in seen:
            continue

        seen.add(resolved)

        cs = max(0, m.start() - 120)
        ce = min(len(markdown), m.end() + 120)

        raw = markdown[cs:ce].replace("\n", " ")
        rs = m.start() - cs
        re_ = m.end() - cs

        ctx = (
            raw[:rs].rstrip()
            + f" <<{anchor}>> "
            + raw[re_:].lstrip()
        ).strip()

        out.append({
            "anchor": anchor,
            "url": resolved,
            "context": ctx,
        })

    return out


# ── Embedding scoring ───────────────────────────────────────────────────────────

def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in one request via Ollama's /api/embed.

    Batched on purpose: at depth N with branching, per-link round-trips add up.
    Uses stdlib only so the diagnostic runs without the ollama client installed.
    """
    if not texts:
        return []

    url = f"{OLLAMA_HOST.rstrip('/')}/api/embed"
    payload = json.dumps({
        "model": OLLAMA_EMBED_MODEL,
        "input": texts,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    embeddings = body.get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        raise RuntimeError(
            f"unexpected embed response: got "
            f"{len(embeddings) if embeddings else 0} vectors for {len(texts)} inputs"
        )

    return embeddings


def _embed_query(topics: list[str]) -> list[float]:
    """Embed the topic set as a single query string (direct analogue of the
    BM25 bag-of-topics query). Returns one query vector reused for all links.

    Alternative not used here: embed each topic separately and take the max
    similarity per link — more faithful to 'relevant to ANY topic' but costs
    more. Try it if the joined query looks mushy.
    """
    joined = " ".join(topics)
    return _embed_batch([QUERY_PREFIX + joined])[0]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _score_links(links: list[dict], query_vec: list[float]) -> list[dict]:
    """Score each link by cosine similarity to the topic query, then normalise
    per-page to 0-1. Same output contract as the BM25 version: attaches
    raw_score / score / rank / follow, returns sorted descending.
    """
    if not links:
        return []

    # Build the same doc text the BM25 version uses: anchor + context + url.
    # URL included because paths reveal intent (/sustainability, /esg, /report).
    doc_texts = []
    for lk in links:
        clean_ctx = lk["context"].replace(f"<<{lk['anchor']}>>", "").strip()
        doc_texts.append(DOC_PREFIX + f"{lk['anchor']} {clean_ctx} {lk['url']}")

    doc_vecs = _embed_batch(doc_texts)

    raw_scores = [_cosine(query_vec, dv) for dv in doc_vecs]
    max_score = max(raw_scores) if raw_scores else 0.0

    for lk, raw in zip(links, raw_scores):
        lk["raw_score"] = raw
        lk["score"] = raw / max_score if max_score > 0 else 0.0

    scored = sorted(links, key=lambda x: x["score"], reverse=True)

    for i, lk in enumerate(scored):
        lk["rank"] = i + 1
        lk["follow"] = lk["score"] >= THRESHOLD or i < TOP_K_PER_PAGE

    return scored


# ── Display (unchanged) ─────────────────────────────────────────────────────────

def _display_path(url: str, seed: str) -> str:
    seed_host = urlparse(seed).netloc.replace("www.", "")
    parsed = urlparse(url)
    link_host = parsed.netloc.replace("www.", "")

    if link_host == seed_host:
        return parsed.path.rstrip("/") or "/"

    return url


def _print_link_row(lk: dict, seed: str, indent: str, visited: set) -> None:
    decision = "FOLLOW" if lk.get("follow") else "SKIP  "
    path = _display_path(lk["url"], seed)
    anchor = lk["anchor"]

    note = ""
    if decision.strip() == "FOLLOW" and lk["url"] in visited:
        note = "  (already visited)"
    elif decision.strip() == "FOLLOW" and lk["rank"] <= TOP_K_PER_PAGE and lk["score"] < THRESHOLD:
        note = "  (top-k fallback)"

    score_col = f"[{lk['score']:.2f}]"
    raw_col = f"(cos {lk['raw_score']:.3f})"
    path_col = path[:46] if len(path) <= 46 else path[:45] + "~"
    anchor_col = anchor[:40] if len(anchor) <= 40 else anchor[:39] + "~"

    print(f"{indent}  {score_col} {raw_col} {decision} -> {path_col:<46}  '{anchor_col}'{note}")


# ── Core crawl (unchanged except the scorer it calls) ───────────────────────────

def _crawl(
    url: str,
    seed: str,
    depth: int,
    visited: set[str],
    stats: dict,
    app,
    query_vec: list[float],
    indent: str,
    dry_run: bool = False,
) -> None:
    if url in visited:
        return

    visited.add(url)

    try:
        markdown, from_cache = _fetch(url, app)
    except Exception as exc:
        print(f"{indent}FETCH FAILED: {url}")
        print(f"{indent}  error: {exc}")
        return

    words = len(markdown.split())
    origin = "cached" if from_cache else "live"

    print(f"{indent}FETCHED [{origin}]: {url} -- {words:,} words")

    d = stats.setdefault(depth, {
        "pages": 0,
        "words": 0,
        "followed": 0,
        "skipped": 0,
    })

    d["pages"] += 1
    d["words"] += words

    if not markdown.strip():
        print(f"{indent}  (empty page -- nothing to extract)")
        print()
        return

    links = _extract_links(markdown, url)

    if not links:
        print(f"{indent}  (no same-domain links found)")
        print()
        return

    print(f"{indent}  {len(links)} same-domain link(s) -- depth {depth}")

    try:
        scored = _score_links(links, query_vec)
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"{indent}  SCORING FAILED (embedding endpoint): {exc}")
        print(f"{indent}  -> is the Ollama host reachable from here? ({OLLAMA_HOST})")
        print()
        return

    for lk in scored:
        _print_link_row(lk, seed, indent, visited)

    n_follow = sum(1 for lk in scored if lk.get("follow"))
    n_skip = len(scored) - n_follow

    if depth >= MAX_DEPTH:
        if n_follow:
            print(f"{indent}  (max depth {MAX_DEPTH} -- {n_follow} FOLLOW link(s) not fetched)")
        d["skipped"] += n_follow + n_skip
        print()
        return

    d["skipped"] += n_skip

    if dry_run:
        if n_follow:
            print(f"{indent}  (dry-run -- {n_follow} FOLLOW link(s) not fetched)")
        print()
        return

    d["followed"] += n_follow
    print()

    for lk in scored:
        if lk.get("follow") and lk["url"] not in visited:
            _crawl(
                url=lk["url"],
                seed=seed,
                depth=depth + 1,
                visited=visited,
                stats=stats,
                app=app,
                query_vec=query_vec,
                indent=indent + "  ",
                dry_run=False,
            )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Guided-crawl trace diagnostic (embedding scorer)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and score the seed page only; do not fetch subpages.",
    )

    args = parser.parse_args()
    dry_run = args.dry_run

    try:
        from firecrawl import V1FirecrawlApp  # type: ignore[import]
    except ImportError:
        print("ERROR: pip install firecrawl-py python-dotenv")
        return

    api_key = os.getenv("FIRECRAWL_API_KEY")

    if not api_key:
        print("ERROR: FIRECRAWL_API_KEY not set in .env or environment")
        return

    # Embed the query once up front — also acts as a reachability check before
    # the crawl starts, so a dead endpoint fails immediately, not mid-trace.
    print()
    print("CRAWL TRACE [embedding]" + ("  [dry-run]" if dry_run else ""))
    print(f"  seed      : {URL}")
    print(f"  max depth : {'--' if dry_run else MAX_DEPTH}")
    print(f"  threshold : {THRESHOLD}  (cosine, tuned separately from BM25)")
    print(f"  top-k     : {TOP_K_PER_PAGE}")
    print(f"  scorer    : nomic-embed-text cosine + per-page normalisation (NO keyword boosts)")
    print(f"  ollama    : {OLLAMA_HOST}  ({OLLAMA_EMBED_MODEL})")
    print(f"  topics    : {len(TOPICS)}")
    print(f"  query     : {' | '.join(TOPICS)}")
    print()

    try:
        query_vec = _embed_query(TOPICS)
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"ERROR: could not embed query — is {OLLAMA_HOST} reachable?")
        print(f"       {exc}")
        return

    print(f"  query embedded OK ({len(query_vec)} dims)")
    print()

    app = V1FirecrawlApp(api_key=api_key)
    visited: set[str] = set()
    stats: dict = {}

    print("=" * 72)
    print()

    _crawl(
        url=URL,
        seed=URL,
        depth=0,
        visited=visited,
        stats=stats,
        app=app,
        query_vec=query_vec,
        indent="",
        dry_run=dry_run,
    )

    print("=" * 72)
    print()
    print("CRAWL SUMMARY")

    total_pages = 0
    total_words = 0

    for depth in sorted(stats):
        s = stats[depth]

        total_pages += s["pages"]
        total_words += s["words"]

        pages_str = f"{s['pages']} page{'s' if s['pages'] != 1 else ''} fetched"
        skip_str = f"{s['skipped']} skipped" if s["skipped"] else ""
        follow_str = f"{s['followed']} followed" if s["followed"] else ""
        depth_tag = " (max depth)" if depth == MAX_DEPTH else ""

        parts = [pages_str]

        if follow_str:
            parts.append(follow_str)
        if skip_str:
            parts.append(skip_str)

        print(f"  Depth {depth}: {', '.join(parts)}{depth_tag}")

    print(f"  Total pages in cache : {total_pages}")
    print(f"  Total words acquired : {total_words:,}")
    print()


if __name__ == "__main__":
    main()
