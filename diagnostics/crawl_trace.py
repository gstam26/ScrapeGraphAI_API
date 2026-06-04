"""
Crawl trace diagnostic.

Guided crawler diagnostic using BM25 lexical relevance scoring.

Features:
  - Firecrawl cache-first fetching
  - Same-domain link extraction
  - BM25 scoring over anchor + context + URL
  - URL/anchor boosts for high-value crawl paths
  - Per-page score normalisation to 0-1
  - Threshold + top-k fallback
  - Human-readable FOLLOW / SKIP trace

Usage:
    python diagnostics/crawl_trace.py
    python diagnostics/crawl_trace.py --dry-run

Requires:
    FIRECRAWL_API_KEY in .env
    pip install firecrawl-py python-dotenv
"""

import argparse
import hashlib
import math
import os
import re
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_REPO_ROOT, "cache")

# ── Crawl config ──────────────────────────────────────────────────────────────

URL = "https://www.oatly.com/en-us"

MAX_DEPTH = 3
THRESHOLD = 0.30
TOP_K_PER_PAGE = 3
FETCH_TIMEOUT_MS = 60_000

BM25_K1 = 1.5
BM25_B = 0.75

_STOP = frozenset({
    "and", "the", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "an", "as", "is", "are", "be", "not",
    "from", "that", "this", "it", "its", "your", "our", "their",
    "you", "we", "they", "was", "were", "has", "have", "had",
    "more", "read", "view", "all", "shop", "buy", "learn",
})

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
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.md")


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


# ── Link extraction ───────────────────────────────────────────────────────────

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


# ── BM25 scoring ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) >= 3 and w not in _STOP
    ]


def _build_query_tokens(topics: list[str]) -> list[str]:
    return _tokenize(" ".join(topics))


def _bm25_prepare(tokenized_docs: list[list[str]]) -> tuple[list[dict[str, int]], dict[str, float], float]:
    if not tokenized_docs:
        return [], {}, 0.0

    tf_docs: list[dict[str, int]] = []
    doc_freq: dict[str, int] = {}

    for doc in tokenized_docs:
        tf: dict[str, int] = {}

        for term in doc:
            tf[term] = tf.get(term, 0) + 1

        for term in set(doc):
            doc_freq[term] = doc_freq.get(term, 0) + 1

        tf_docs.append(tf)

    n_docs = len(tokenized_docs)
    avg_doc_len = sum(len(doc) for doc in tokenized_docs) / max(n_docs, 1)

    idf = {
        term: max(0.0, math.log(1 + ((n_docs - df + 0.5) / (df + 0.5))))
        for term, df in doc_freq.items()
    }

    return tf_docs, idf, avg_doc_len


def _bm25_score_doc(
    query_tokens: list[str],
    doc_tokens: list[str],
    tf: dict[str, int],
    idf: dict[str, float],
    avg_doc_len: float,
) -> float:
    if not query_tokens or not doc_tokens or avg_doc_len <= 0:
        return 0.0

    doc_len = len(doc_tokens)
    score = 0.0

    # Use unique query terms so repeated topic words do not dominate.
    for term in set(query_tokens):
        freq = tf.get(term, 0)

        if freq == 0:
            continue

        denom = freq + BM25_K1 * (1 - BM25_B + BM25_B * (doc_len / avg_doc_len))
        score += idf.get(term, 0.0) * ((freq * (BM25_K1 + 1)) / denom)

    return score


def _url_anchor_boost(url: str, anchor: str) -> float:
    text = f"{url} {anchor}".lower()

    boosts = {
        "sustainability": 0.35,
        "sustainable": 0.25,
        "sustainability-report": 0.35,
        "report": 0.25,
        "annual-report": 0.25,
        "esg": 0.25,
        "impact": 0.20,
        "responsibility": 0.20,
        "climate": 0.20,
        "environment": 0.20,
        "emissions": 0.20,
        "carbon": 0.18,
        "net-zero": 0.18,
        "netzero": 0.18,
        "planet": 0.12,
    }

    return sum(weight for term, weight in boosts.items() if term in text)


def _score_links(links: list[dict], query_tokens: list[str]) -> list[dict]:
    """Score each link using BM25 over anchor + context + URL, then normalise 0-1."""
    if not links:
        return []

    doc_texts = []

    for lk in links:
        clean_ctx = lk["context"].replace(f"<<{lk['anchor']}>>", "").strip()

        # URL is included because paths often reveal intent:
        # /sustainability, /esg, /impact, /report, etc.
        doc_texts.append(f"{lk['anchor']} {clean_ctx} {lk['url']}")

    tokenized_docs = [_tokenize(text) for text in doc_texts]
    tf_docs, idf, avg_doc_len = _bm25_prepare(tokenized_docs)

    raw_scores = []

    for lk, doc_tokens, tf in zip(links, tokenized_docs, tf_docs):
        raw = _bm25_score_doc(query_tokens, doc_tokens, tf, idf, avg_doc_len)
        raw += _url_anchor_boost(lk["url"], lk["anchor"])
        raw_scores.append(raw)

    max_score = max(raw_scores) if raw_scores else 0.0

    for lk, raw in zip(links, raw_scores):
        lk["raw_score"] = raw
        lk["score"] = raw / max_score if max_score > 0 else 0.0

    scored = sorted(links, key=lambda x: x["score"], reverse=True)

    for i, lk in enumerate(scored):
        lk["rank"] = i + 1
        lk["follow"] = lk["score"] >= THRESHOLD or i < TOP_K_PER_PAGE

    return scored


# ── Display ───────────────────────────────────────────────────────────────────

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
    path_col = path[:52] if len(path) <= 52 else path[:51] + "~"
    anchor_col = anchor[:48] if len(anchor) <= 48 else anchor[:47] + "~"

    print(f"{indent}  {score_col} {decision} -> {path_col:<52}  '{anchor_col}'{note}")


# ── Core crawl ────────────────────────────────────────────────────────────────

def _crawl(
    url: str,
    seed: str,
    depth: int,
    visited: set[str],
    stats: dict,
    app,
    query_tokens: list[str],
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

    scored = _score_links(links, query_tokens)

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
                query_tokens=query_tokens,
                indent=indent + "  ",
                dry_run=False,
            )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Guided-crawl trace diagnostic")
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

    query_tokens = _build_query_tokens(TOPICS)

    print()
    print("CRAWL TRACE" + ("  [dry-run]" if dry_run else ""))
    print(f"  seed      : {URL}")
    print(f"  max depth : {'--' if dry_run else MAX_DEPTH}")
    print(f"  threshold : {THRESHOLD}")
    print(f"  top-k     : {TOP_K_PER_PAGE}")
    print(f"  scorer    : BM25 + URL/anchor boosts + per-page normalisation")
    print(f"  topics    : {len(TOPICS)}")
    print()
    print(f"  Query terms: {len(set(query_tokens))} unique")
    print(f"  Query      : {' | '.join(TOPICS)}")
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
        query_tokens=query_tokens,
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