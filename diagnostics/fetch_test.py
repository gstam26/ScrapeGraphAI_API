"""
Firecrawl fetch diagnostic.

For each URL: fetches markdown via Firecrawl's scrape endpoint, saves to
cache/<sha256(url)>.md, and prints status / word count / content quality.

No extraction, no scoring — just: did we get usable content?

Usage:
    python diagnostics/fetch_test.py

Requires FIRECRAWL_API_KEY in .env or environment.
"""

import os
import hashlib
from dotenv import load_dotenv

load_dotenv()

URLS = [
    "https://oatly.com",
    "https://www.ripplefoods.com",
    "https://www.califiafarms.com",
    "https://www.silk.com",
    "https://www.elmhurst1925.com",
]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_REPO_ROOT, "cache")

# Firecrawl fetch timeout in ms — these pages can be slow to render
FETCH_TIMEOUT_MS = 60_000


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(url: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.md")


# ---------------------------------------------------------------------------
# Content quality heuristics
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return len(text.split())


def _assess(markdown: str) -> str:
    """
    Classify content as good body text, nav/footer heavy, or mixed.

    Prose lines  (>= 8 words) → real sentences
    Short lines  (<= 3 words) → likely nav items, link lists, headings

    Thresholds (empirically reasonable for plant-brand pages):
      prose_ratio >= 0.25  → good body text
      short_ratio >= 0.65  → nav/footer heavy
      otherwise            → mixed
    """
    lines = [ln.strip() for ln in markdown.splitlines() if ln.strip()]
    if not lines:
        return "empty"

    n = len(lines)
    prose = sum(1 for ln in lines if len(ln.split()) >= 8)
    short = sum(1 for ln in lines if len(ln.split()) <= 3)

    if _word_count(markdown) < 80:
        return "too short"

    prose_ratio = prose / n
    short_ratio = short / n

    if prose_ratio >= 0.25:
        return "good body text"
    if short_ratio >= 0.65:
        return "nav/footer heavy"
    return "mixed"


def _first_prose_line(markdown: str, min_words: int = 10, max_chars: int = 110) -> str:
    """Return the first line that reads like a real sentence."""
    for line in markdown.splitlines():
        line = line.strip()
        # Skip markdown headings and link-heavy lines
        if line.startswith("#") or line.count("[") > 2:
            continue
        if len(line.split()) >= min_words:
            return line[:max_chars] + ("…" if len(line) > max_chars else "")
    return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("ERROR: FIRECRAWL_API_KEY not set in environment or .env")
        return

    try:
        from firecrawl import V1FirecrawlApp  # type: ignore[import]
    except ImportError:
        print("ERROR: firecrawl-py not installed. Run: pip install firecrawl-py")
        return

    app = V1FirecrawlApp(api_key=api_key)

    ok_count = 0
    total_words = 0

    col_url = 48
    print(f"\n  {'URL':<{col_url}}  {'STATUS':<6}  {'WORDS':>6}  CONTENT QUALITY")
    print(f"  {'-' * col_url}  {'------':<6}  {'------':>6}  ---------------")

    for url in URLS:
        short = url if len(url) <= col_url else url[: col_url - 1] + "…"
        try:
            result = app.scrape_url(
                url,
                formats=["markdown"],
                timeout=FETCH_TIMEOUT_MS,
            )

            if not result.success or not result.markdown:
                err = result.error or "no markdown returned"
                print(f"  {short:<{col_url}}  {'FAIL':<6}  {'':>6}  {err}")
                continue

            markdown = result.markdown.strip()

            cache_file = _cache_path(url)
            with open(cache_file, "w", encoding="utf-8") as fh:
                fh.write(markdown)

            words = _word_count(markdown)
            quality = _assess(markdown)
            preview = _first_prose_line(markdown)

            ok_count += 1
            total_words += words

            print(f"  {short:<{col_url}}  {'ok':<6}  {words:>6}  {quality}")
            if preview:
                print(f"    {preview}")
            print(f"    → saved {cache_file}")

        except Exception as exc:
            print(f"  {short:<{col_url}}  {'FAIL':<6}  {'':>6}  {exc}")

    # Summary
    print(f"\n  {ok_count}/{len(URLS)} pages fetched successfully", end="")
    if ok_count:
        print(f"  |  avg {total_words // ok_count:,} words/page", end="")
    print("\n")


if __name__ == "__main__":
    main()
