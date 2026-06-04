"""
Crawl trace diagnostic.

Simulates a full guided crawl from a single seed URL and prints a
complete human-readable trace of every decision made:

  1. Fetch the seed page via Firecrawl (cache-first) and print word count
  2. Extract all same-domain links with anchor text + surrounding context
  3. Score every link against a hardcoded topic list using
     sentence-transformers cosine similarity
  4. Print a scored link table showing FOLLOW / SKIP decisions
  5. Fetch the FOLLOW links (score >= threshold), indent one level, repeat
  6. Recurse up to MAX_DEPTH; never fetch the same URL twice (visited set)
  7. Print a crawl summary by depth

No pipeline imports.  Reads/writes only cache/.

Usage (from project root):
    python diagnostics/crawl_trace.py             # full crawl
    python diagnostics/crawl_trace.py --dry-run   # fetch + score seed only

Requires:
    FIRECRAWL_API_KEY in .env
    pip install firecrawl-py
"""

import argparse
import os
import re
import hashlib
from dotenv import load_dotenv
from urllib.parse import urljoin, urlparse

load_dotenv()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR  = os.path.join(_REPO_ROOT, "cache")

# ── Crawl config ──────────────────────────────────────────────────────────────
URL              = "https://www.ripplefoods.com"
MAX_DEPTH        = 2
THRESHOLD        = 0.30
FETCH_TIMEOUT_MS = 60_000

_STOP = frozenset({
    "and", "the", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "an", "as", "is", "are", "be", "not",
})

TOPICS = [
    "sustainability environmental impact carbon footprint emissions",
    "climate change net zero targets science based",
    "annual report ESG disclosure climate data",
    "supply chain sourcing farming raw materials",
    "certifications organic B-corp standards labels",
    "about the company mission values story history",
    "products plant-based milk ingredients nutrition",
    "press news media announcements",
]


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(url: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.md")


def _cached(url: str) -> bool:
    return os.path.exists(_cache_path(url))


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _fetch(url: str, app) -> tuple[str, bool]:
    """Return (markdown, from_cache).  Writes to cache/ on a live fetch."""
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
    out  = []

    for m in pattern.finditer(markdown):
        anchor  = m.group(1).strip()
        raw_url = m.group(2).strip()

        if raw_url.startswith(("mailto:", "javascript:", "#", "tel:", "data:")):
            continue

        resolved = urljoin(page_url, raw_url).split("#")[0].rstrip("/")
        if not resolved.startswith("http") or not _same_domain(page_url, resolved):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)

        # Context: 100 chars either side; replace the [anchor](url) with <<anchor>>
        cs  = max(0, m.start() - 100)
        ce  = min(len(markdown), m.end() + 100)
        raw = markdown[cs:ce].replace("\n", " ")
        rs  = m.start() - cs
        re_ = m.end()   - cs
        ctx = (raw[:rs].rstrip() + f" <<{anchor}>> " + raw[re_:].lstrip()).strip()

        out.append({"anchor": anchor, "url": resolved, "context": ctx})

    return out


# ── Scoring  (keyword overlap — no external models) ──────────────────────────

def _build_topic_vocab(topics: list[str]) -> frozenset[str]:
    """Unique meaningful words (>= 3 chars, not stop-words) across all topics."""
    vocab: set[str] = set()
    for topic in topics:
        for word in re.findall(r"[a-z]+", topic.lower()):
            if len(word) >= 3 and word not in _STOP:
                vocab.add(word)
    return frozenset(vocab)


def _overlap_score(text: str, topic_vocab: frozenset[str]) -> float:
    """
    Score = |unique_words(text) ∩ topic_vocab| / |unique_words(text)|
    Words shorter than 3 chars are ignored on both sides.
    """
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 3}
    if not words:
        return 0.0
    return len(words & topic_vocab) / len(words)


def _score_links(links: list[dict], topic_vocab: frozenset[str]) -> list[dict]:
    """Score each link by keyword overlap; sort descending."""
    for lk in links:
        clean = lk["context"].replace(f"<<{lk['anchor']}>>", "").strip()
        lk["score"] = _overlap_score(f"{lk['anchor']} {clean}", topic_vocab)
    return sorted(links, key=lambda x: x["score"], reverse=True)


# ── Display ───────────────────────────────────────────────────────────────────

def _display_path(url: str, seed: str) -> str:
    """Return just the URL path when same host as seed, else full URL."""
    seed_host = urlparse(seed).netloc.replace("www.", "")
    parsed    = urlparse(url)
    link_host = parsed.netloc.replace("www.", "")
    if link_host == seed_host:
        return parsed.path.rstrip("/") or "/"
    return url


def _print_link_row(lk: dict, seed: str, indent: str, visited: set) -> None:
    decision = "FOLLOW" if lk["score"] >= THRESHOLD else "SKIP  "
    path     = _display_path(lk["url"], seed)
    anchor   = lk["anchor"]
    # Make deduplication visible: a FOLLOW that won't be fetched because the
    # URL was already fetched in an earlier branch of the crawl.
    note     = "  (already visited)" if decision.strip() == "FOLLOW" and lk["url"] in visited else ""

    score_col  = f"[{lk['score']:.2f}]"
    path_col   = path[:52] if len(path) <= 52 else path[:51] + "~"
    anchor_col = anchor[:48] if len(anchor) <= 48 else anchor[:47] + "~"

    print(f"{indent}  {score_col} {decision} -> {path_col:<52}  '{anchor_col}'{note}")


# ── Core crawl ────────────────────────────────────────────────────────────────

def _crawl(
    url:         str,
    seed:        str,
    depth:       int,
    visited:     set,
    stats:       dict,
    app,
    topic_vocab: frozenset,
    indent:      str,
    dry_run:     bool = False,
) -> None:
    if url in visited:
        return
    visited.add(url)

    # 1. Fetch
    try:
        markdown, from_cache = _fetch(url, app)
    except Exception as exc:
        print(f"{indent}FETCH FAILED: {url}")
        print(f"{indent}  error: {exc}")
        return

    words  = len(markdown.split())
    origin = "cached" if from_cache else "live"
    print(f"{indent}FETCHED [{origin}]: {url} -- {words:,} words")

    d = stats.setdefault(depth, {"pages": 0, "words": 0, "followed": 0, "skipped": 0})
    d["pages"] += 1
    d["words"] += words

    if not markdown.strip():
        print(f"{indent}  (empty page -- nothing to extract)")
        print()
        return

    # 2. Extract links
    links = _extract_links(markdown, url)
    if not links:
        print(f"{indent}  (no same-domain links found)")
        print()
        return

    print(f"{indent}  {len(links)} same-domain link(s) -- depth {depth}")

    # 3. Score
    scored = _score_links(links, topic_vocab)

    # 4. Print scored table
    for lk in scored:
        _print_link_row(lk, seed, indent, visited)

    n_above = sum(1 for lk in scored if lk["score"] >= THRESHOLD)
    n_below = len(scored) - n_above

    # At max depth: FOLLOW links are noted but not fetched
    if depth >= MAX_DEPTH:
        if n_above:
            print(f"{indent}  (max depth {MAX_DEPTH} -- {n_above} FOLLOW link(s) not fetched)")
        d["skipped"] += n_above + n_below
        print()
        return

    d["skipped"] += n_below

    # 5. Recurse into FOLLOW links (skipped entirely in dry-run mode)
    if dry_run:
        if n_above:
            print(f"{indent}  (dry-run -- {n_above} FOLLOW link(s) not fetched)")
        print()
        return

    d["followed"] += n_above
    print()

    for lk in scored:
        if lk["score"] >= THRESHOLD and lk["url"] not in visited:
            _crawl(
                url=lk["url"],
                seed=seed,
                depth=depth + 1,
                visited=visited,
                stats=stats,
                app=app,
                topic_vocab=topic_vocab,
                indent=indent + "  ",
                dry_run=False,
            )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Guided-crawl trace diagnostic")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and score the seed page only; do not fetch any subpages.",
    )
    args = parser.parse_args()
    dry_run = args.dry_run

    # Dependency checks
    try:
        from firecrawl import V1FirecrawlApp  # type: ignore[import]
    except ImportError:
        print("ERROR: pip install firecrawl-py")
        return

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("ERROR: FIRECRAWL_API_KEY not set in .env or environment")
        return

    # Header
    print()
    print("CRAWL TRACE" + ("  [dry-run]" if dry_run else ""))
    print(f"  seed      : {URL}")
    print(f"  max depth : {'--' if dry_run else MAX_DEPTH}")
    print(f"  threshold : {THRESHOLD}")
    print(f"  topics    : {len(TOPICS)}")
    print()

    topic_vocab = _build_topic_vocab(TOPICS)
    print(f"  Topic vocabulary: {len(topic_vocab)} words")
    print()

    app     = V1FirecrawlApp(api_key=api_key)
    visited: set  = set()
    stats:   dict = {}

    print("=" * 72)
    print()

    _crawl(
        url=URL, seed=URL, depth=0,
        visited=visited, stats=stats,
        app=app, topic_vocab=topic_vocab,
        indent="",
        dry_run=dry_run,
    )

    # Summary
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
        skip_str  = f"{s['skipped']} skipped" if s["skipped"] else ""
        depth_tag = " (max depth)" if depth == MAX_DEPTH else ""

        parts = [pages_str]
        if skip_str:
            parts.append(skip_str)

        print(f"  Depth {depth}: {', '.join(parts)}{depth_tag}")

    print(f"  Total pages in cache : {total_pages}")
    print(f"  Total words acquired : {total_words:,}")
    print()


if __name__ == "__main__":
    main()
