"""
Link extractor diagnostic.

Reads a cached markdown file (written by diagnostics/fetch_test.py),
extracts all hyperlinks using regex, filters to same-domain links only,
and prints a ranked table (page order) with:
  - anchor text
  - surrounding context (up to 100 chars either side of the link)
  - full resolved URL

No fetching, no scoring, no pipeline imports.

Usage (from project root):
    python diagnostics/link_extractor.py
"""

import os
import re
import hashlib
from urllib.parse import urljoin, urlparse

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_REPO_ROOT, "cache")

# Change to any URL that fetch_test.py (or acquire.py) has already cached.
URL = "https://oatly.com"


# ---------------------------------------------------------------------------
# Cache lookup — tries .md (fetch_test.py) then .txt (acquire.py)
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> str | None:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    for ext in (".md", ".txt"):
        path = os.path.join(CACHE_DIR, f"{key}{ext}")
        if os.path.exists(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

def _same_domain(base_url: str, candidate_url: str) -> bool:
    """True if candidate shares the root domain with base (ignores www.)."""
    base = urlparse(base_url).netloc.replace("www.", "")
    cand = urlparse(candidate_url).netloc.replace("www.", "")
    return cand == base or cand.endswith("." + base)


def _extract_links(markdown: str, base_url: str) -> list[dict]:
    """
    Find all [anchor](url) links in markdown, resolve to absolute URLs,
    filter to same-domain, deduplicate, return in page order.

    Each entry: {position, anchor, url, context}
    Context replaces the raw link with «anchor» for readability.
    """
    # Matches [anchor text](url) and [anchor text](url "optional title")
    pattern = re.compile(r'\[([^\]]+)\]\(([^)\s"]+)(?:\s+"[^"]*")?\)')

    seen_urls: set[str] = set()
    results = []

    for match in pattern.finditer(markdown):
        anchor = match.group(1).strip()
        raw_url = match.group(2).strip()

        # Skip non-navigational schemes
        if raw_url.startswith(("mailto:", "javascript:", "#", "tel:", "data:")):
            continue

        resolved = urljoin(base_url, raw_url)
        # Normalise: strip fragment and trailing slash
        resolved = resolved.split("#")[0].rstrip("/")

        if not resolved.startswith("http"):
            continue

        if not _same_domain(base_url, resolved):
            continue

        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)

        # Context: up to 100 chars before and after the full [anchor](url) match,
        # with the link replaced by «anchor» so the URL clutter is removed.
        ctx_start = max(0, match.start() - 100)
        ctx_end   = min(len(markdown), match.end() + 100)
        raw_ctx   = markdown[ctx_start:ctx_end].replace("\n", " ")

        rel_start = match.start() - ctx_start
        rel_end   = match.end()   - ctx_start
        context = (
            raw_ctx[:rel_start].rstrip()
            + f" «{anchor}» "
            + raw_ctx[rel_end:].lstrip()
        ).strip()

        results.append({
            "position": match.start(),
            "anchor":   anchor,
            "url":      resolved,
            "context":  context,
        })

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cache_file = _cache_path(URL)

    if not cache_file:
        key = hashlib.sha256(URL.encode("utf-8")).hexdigest()
        print(f"\nERROR: no cache file found for:\n  {URL}")
        print(f"\n  Expected one of:")
        print(f"    {os.path.join('cache', key + '.md')}   (fetch_test.py)")
        print(f"    {os.path.join('cache', key + '.txt')}  (acquire.py)")
        print(f"\n  Run diagnostics/fetch_test.py first to populate the cache.")
        return

    with open(cache_file, "r", encoding="utf-8") as fh:
        markdown = fh.read()

    links = _extract_links(markdown, URL)

    rel_cache = os.path.relpath(cache_file, _REPO_ROOT)
    print(f"\nSource  : {URL}")
    print(f"Cache   : {rel_cache}")
    print(f"Content : {len(markdown):,} chars  |  {len(markdown.split()):,} words")
    print(f"Links   : {len(links)} unique same-domain link(s)\n")

    if not links:
        print("  (no same-domain links found — page may be nav-light or fully JS-rendered)")
        return

    W_NUM    = 3
    W_ANCHOR = 32
    W_URL    = 58

    print(f"  {'#':>{W_NUM}}  {'ANCHOR':<{W_ANCHOR}}  {'RESOLVED URL':<{W_URL}}")
    print(f"  {'-' * W_NUM}  {'-' * W_ANCHOR}  {'-' * W_URL}")

    for i, link in enumerate(links, 1):
        anchor = link["anchor"]
        url    = link["url"]
        ctx    = link["context"]

        a = anchor[:W_ANCHOR]     if len(anchor) <= W_ANCHOR else anchor[:W_ANCHOR - 1] + "…"
        u = url[:W_URL]           if len(url)    <= W_URL    else url[:W_URL - 1]       + "…"

        print(f"  {i:>{W_NUM}}  {a:<{W_ANCHOR}}  {u:<{W_URL}}")
        print(f"         context: {ctx}")
        print()

    print(f"  {len(links)} link(s) total\n")


if __name__ == "__main__":
    main()
