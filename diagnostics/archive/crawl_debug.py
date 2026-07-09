"""
Crawl scoring diagnostic.

Reads a cached markdown file, extracts same-domain links, scores each
one against a set of hardcoded topics using sentence-transformers cosine
similarity (anchor text + context concatenated), and prints a ranked
table with a threshold line separating follow/skip decisions.

No fetching, no pipeline imports — reads only from cache/.

Usage (from project root):
    python diagnostics/crawl_debug.py

No external models or API keys required beyond FIRECRAWL_API_KEY.
"""

import os
import re
import sys
from urllib.parse import urljoin, urlparse

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR  = os.path.join(_REPO_ROOT, "cache")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.acquire.cache import cache_path_any

# ── Hardcoded target URL (must already be cached by fetch_test.py) ──────────
URL = "https://oatly.com"

# ── Relevance signal: what are we trying to find on this website? ────────────
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

# ── Score threshold separating follow / skip ─────────────────────────────────
THRESHOLD = 0.30

# ── Stop-words excluded when building the topic vocabulary ────────────────────
_STOP = frozenset({
    "and", "the", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "an", "as", "is", "are", "be", "not",
})


# ─────────────────────────────────────────────────────────────────────────────
# Link extraction  (self-contained copy — no imports from link_extractor.py)
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(url: str) -> str | None:
    return cache_path_any(url, cache_dir=CACHE_DIR)


def _same_domain(base_url: str, candidate_url: str) -> bool:
    base = urlparse(base_url).netloc.replace("www.", "")
    cand = urlparse(candidate_url).netloc.replace("www.", "")
    return cand == base or cand.endswith("." + base)


def _extract_links(markdown: str, base_url: str) -> list[dict]:
    pattern = re.compile(r'\[([^\]]+)\]\(([^)\s"]+)(?:\s+"[^"]*")?\)')
    seen: set[str] = set()
    results = []

    for match in pattern.finditer(markdown):
        anchor  = match.group(1).strip()
        raw_url = match.group(2).strip()

        if raw_url.startswith(("mailto:", "javascript:", "#", "tel:", "data:")):
            continue

        resolved = urljoin(base_url, raw_url).split("#")[0].rstrip("/")
        if not resolved.startswith("http"):
            continue
        if not _same_domain(base_url, resolved):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)

        ctx_start = max(0, match.start() - 100)
        ctx_end   = min(len(markdown), match.end() + 100)
        raw_ctx   = markdown[ctx_start:ctx_end].replace("\n", " ")
        rel_s     = match.start() - ctx_start
        rel_e     = match.end()   - ctx_start
        context   = (
            raw_ctx[:rel_s].rstrip()
            + f" <<{anchor}>> "
            + raw_ctx[rel_e:].lstrip()
        ).strip()

        results.append({
            "position": match.start(),
            "anchor":   anchor,
            "url":      resolved,
            "context":  context,
        })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Scoring  (keyword overlap — no external models)
# ─────────────────────────────────────────────────────────────────────────────

def _build_topic_vocab(topics: list[str]) -> frozenset[str]:
    """Unique meaningful words (>= 3 chars, not stop-words) across all topics."""
    vocab: set[str] = set()
    for topic in topics:
        for word in re.findall(r"[a-z]+", topic.lower()):
            if len(word) >= 3 and word not in _STOP:
                vocab.add(word)
    return frozenset(vocab)


def _overlap_score(text: str, topic_vocab: frozenset[str]) -> tuple[float, list[str]]:
    """
    Score = |unique_words(text) ∩ topic_vocab| / |unique_words(text)|

    Returns (score, sorted list of matched words).
    Words shorter than 3 chars are ignored on both sides.
    """
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 3}
    if not words:
        return 0.0, []
    matched = sorted(words & topic_vocab)
    return len(matched) / len(words), matched


def _score_links(links: list[dict], topic_vocab: frozenset[str]) -> list[dict]:
    """Score each link by keyword overlap; attach matched words; sort descending."""
    for lk in links:
        clean = lk["context"].replace(f"<<{lk['anchor']}>>", "").strip()
        lk["score"], lk["matched"] = _overlap_score(
            f"{lk['anchor']} {clean}", topic_vocab
        )
    return sorted(links, key=lambda x: x["score"], reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

W_SCORE  = 5
W_ANCHOR = 28
W_URL    = 55


def _trunc(s: str, width: int) -> str:
    return s if len(s) <= width else s[:width - 1] + "…"


def _print_row(rank: int, link: dict) -> None:
    score   = f"{link['score']:.3f}"
    anchor  = _trunc(link["anchor"], W_ANCHOR)
    url     = _trunc(link["url"],    W_URL)
    ctx     = _trunc(link["context"], 120)
    matched = ", ".join(link.get("matched", [])) or "(none)"
    print(f"  {score:<{W_SCORE}}  {anchor:<{W_ANCHOR}}  {url:<{W_URL}}")
    print(f"  {'':>{W_SCORE}}  matched: {matched}")
    print(f"  {'':>{W_SCORE}}  ctx:   {ctx}")
    print()


def _print_threshold_line() -> None:
    bar = "-" * 20
    label = f"  threshold {THRESHOLD:.2f}  -- follow above  /  skip below  "
    print(f"\n  {bar}{label}{bar}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    cache_file = _cache_path(URL)
    if not cache_file:
        key = hashlib.sha256(URL.encode("utf-8")).hexdigest()
        print(f"\nERROR: no cache file found for:\n  {URL}")
        print(f"\n  Expected:  cache/{key}.md   (fetch_test.py)")
        print(f"             cache/{key}.txt  (acquire.py)")
        print(f"\n  Run diagnostics/fetch_test.py first.")
        return

    with open(cache_file, "r", encoding="utf-8") as fh:
        markdown = fh.read()

    links = _extract_links(markdown, URL)
    rel_cache = os.path.relpath(cache_file, _REPO_ROOT)

    print(f"\nSource    : {URL}")
    print(f"Cache     : {rel_cache}")
    print(f"Content   : {len(markdown):,} chars  |  {len(markdown.split()):,} words")
    print(f"Links     : {len(links)} unique same-domain link(s)")
    print(f"Threshold : {THRESHOLD}\n")

    if not links:
        print("  (no same-domain links found — nothing to score)")
        return

    print(f"  Topics ({len(TOPICS)}):")
    for t in TOPICS:
        print(f"    · {t}")
    print()

    topic_vocab = _build_topic_vocab(TOPICS)
    scored = _score_links(links, topic_vocab)

    # ── Ranked table ──────────────────────────────────────────────────────────
    header_anchor = f"{'ANCHOR':<{W_ANCHOR}}"
    header_url    = f"{'URL':<{W_URL}}"
    print(f"  {'SCORE':<{W_SCORE}}  {header_anchor}  {header_url}")
    print(f"  {'-'*W_SCORE}  {'-'*W_ANCHOR}  {'-'*W_URL}")
    print()

    n_follow = sum(1 for lk in scored if lk["score"] >= THRESHOLD)
    n_skip   = len(scored) - n_follow
    threshold_printed = False

    for rank, link in enumerate(scored, 1):
        if not threshold_printed and link["score"] < THRESHOLD:
            _print_threshold_line()
            threshold_printed = True
        _print_row(rank, link)

    if not threshold_printed:
        _print_threshold_line()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"  Summary at threshold {THRESHOLD}:")
    print(f"    follow : {n_follow:>3}  link(s)  (score >= {THRESHOLD})")
    print(f"    skip   : {n_skip:>3}  link(s)  (score <  {THRESHOLD})")
    print()


if __name__ == "__main__":
    main()
