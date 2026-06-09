"""
Guided crawl -> final list of relevant URLs.

Crawls from the seed, scores links by semantic similarity (Ollama
nomic-embed-text), follows the ones above a RAW cosine threshold, and prints a
ranked list of the relevant pages it found. This is the Acquire-layer output
(the relevant FetchedPage set) -- not a per-decision trace.

Quality/speed fixes vs the trace version:
  - Thresholds on RAW cosine, NOT per-page-normalised score.
  - No blanket top-k fallback.
  - Link hygiene: images / base64 / asset-CDN links dropped before scoring.
  - URL canonicalisation for dedup; CLEANED urls emitted in the final list
    (modal/utm/fragment stripped) so the output is safe to feed downstream.
  - Hard page budget: crawl stops after MAX_PAGES fetches.

Usage:
    python diagnostics/crawl_collect.py

Requires:
    FIRECRAWL_API_KEY in .env
    Reachable Ollama host (internal network/VPN)
    pip install firecrawl-py python-dotenv
"""

import math
import os
import re
import sys
import json
import time
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

# -- Crawl config --------------------------------------------------------------

URL = "https://www.oatly.com/en-us"

MAX_DEPTH = 2          # balanced
MAX_PAGES = 50         # hard cap -- crawl stops after this many fetches
FETCH_TIMEOUT_MS = 60_000

# Follow a link if its RAW cosine >= this. THE main quality dial.
# Observed bands on this site: real sustainability links ~0.59-0.70;
# borderline (pee-for-planet, future-of-taste) ~0.54-0.56; recipe/nav noise
# ~0.41-0.52. 0.55 keeps the borderline-relevant in, noise out. Raise for
# precision, lower for recall.
FOLLOW_THRESHOLD = 0.55

# -- Ollama embedding config (mirrors config.py) -------------------------------

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://10.99.96.1:11434")
OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_TIMEOUT = 60
OLLAMA_KEEP_ALIVE = "10m"

QUERY_PREFIX = "search_query: "
DOC_PREFIX = "search_document: "

TOPICS = [
    "sustainability", "sustainability report", "climate", "environment",
    "impact", "responsibility", "emissions", "carbon", "net zero", "ESG",
    "planet", "annual report", "social responsibility",
    "sustainable packaging", "renewable energy",
]

_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
            ".css", ".js", ".mp4", ".webm")
_ASSET_HOST_PREFIXES = ("assets.", "cdn.", "static.")
_LOCALE_RE = re.compile(r"^/[a-z]{2}-[a-z]{2}(/|$)")
_NOISE_PARAMS = ("modal",)   # plus anything starting with utm_


# -- Cache / fetch -------------------------------------------------------------

def _cache_path(url: str) -> str:
    return _cache_path_fn(url, cache_dir=CACHE_DIR, ext=".md")


def _fetch(url: str, app) -> tuple[str, bool]:
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


# -- URL handling --------------------------------------------------------------

def _strip_noise_query(query: str) -> str:
    if not query:
        return ""
    keep = []
    for kv in query.split("&"):
        name = kv.split("=")[0].lower()
        if name in _NOISE_PARAMS or name.startswith("utm_"):
            continue
        keep.append(kv)
    return "&".join(keep)


def _clean_display(url: str) -> str:
    """The url we EMIT: drop fragment + noise params, KEEP locale and path.
    Safe to feed to the next stage (no modal popups / tracking)."""
    p = urlparse(url.split("#")[0])
    netloc = p.netloc.lower()
    query = _strip_noise_query(p.query)
    out = f"{p.scheme}://{netloc}{p.path.rstrip('/')}"
    if query:
        out += "?" + query
    return out


def _canonical(url: str) -> str:
    """The DEDUP key: more aggressive -- also drops www and a leading locale
    segment, so /en-us/X and /X collapse to one key."""
    p = urlparse(url.split("#")[0])
    netloc = p.netloc.replace("www.", "").lower()
    path = p.path

    m = _LOCALE_RE.match(path)
    if m:
        cut = m.end() - (1 if m.group(0).endswith("/") else 0)
        path = path[cut:] or "/"

    query = _strip_noise_query(p.query)
    out = f"{p.scheme}://{netloc}{path.rstrip('/')}"
    if query:
        out += "?" + query
    return out or url


def _is_junk_link(url: str, anchor: str) -> bool:
    if "Base64-Image-Removed" in url or "Base64-Image-Removed" in anchor:
        return True
    p = urlparse(url)
    if any(p.netloc.lower().startswith(pre) for pre in _ASSET_HOST_PREFIXES):
        return True
    if p.path.lower().rstrip("/").endswith(_IMG_EXT):
        return True
    return False


# -- Link extraction -----------------------------------------------------------

def _same_domain(base_url: str, candidate_url: str) -> bool:
    base = urlparse(base_url).netloc.replace("www.", "")
    cand = urlparse(candidate_url).netloc.replace("www.", "")
    return cand == base or cand.endswith("." + base)


def _extract_links(markdown: str, page_url: str) -> list[dict]:
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
        if _is_junk_link(resolved, anchor):
            continue

        key = _canonical(resolved)
        if key in seen:
            continue
        seen.add(key)

        cs = max(0, m.start() - 120)
        ce = min(len(markdown), m.end() + 120)
        raw = markdown[cs:ce].replace("\n", " ")
        rs, re_ = m.start() - cs, m.end() - cs
        ctx = (raw[:rs].rstrip() + f" <<{anchor}>> " + raw[re_:].lstrip()).strip()

        out.append({"anchor": anchor, "url": resolved, "context": ctx})

    return out


# -- Embedding scoring (raw cosine, NO normalisation) --------------------------

def _embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    req = urllib.request.Request(
        f"{OLLAMA_HOST.rstrip('/')}/api/embed",
        data=json.dumps({
            "model": OLLAMA_EMBED_MODEL,
            "input": texts,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    embs = body.get("embeddings")
    if not embs or len(embs) != len(texts):
        raise RuntimeError(f"embed returned {len(embs) if embs else 0} for {len(texts)}")
    return embs


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def _score_links(links: list[dict], topics: list[str]) -> list[dict]:
    if not links:
        return []
    query_texts = [QUERY_PREFIX + topic for topic in topics]
    doc_texts = []
    for lk in links:
        marker = f"<<{lk['anchor']}>>"
        clean_ctx = lk["context"].replace(marker, "").strip()
        doc_texts.append(DOC_PREFIX + f"{lk['anchor']} {clean_ctx} {lk['url']}")
    all_vecs = _embed_batch(query_texts + doc_texts)
    q_vecs = all_vecs[:len(topics)]
    d_vecs = all_vecs[len(topics):]
    for lk, dv in zip(links, d_vecs):
        lk["score"] = max(_cosine(qv, dv) for qv in q_vecs)
    return sorted(links, key=lambda x: x["score"], reverse=True)


# -- Crawl ---------------------------------------------------------------------

def _crawl(url, depth, visited, results, state, app, topics):
    if state["fetched"] >= MAX_PAGES:
        return

    key = _canonical(url)
    if key in visited:
        return
    visited.add(key)

    try:
        markdown, _ = _fetch(url, app)
    except Exception:
        state["fetch_failed"] += 1
        return

    state["fetched"] += 1
    if not markdown.strip():
        return

    links = _extract_links(markdown, url)
    if not links:
        return

    scored = _score_links(links, topics)

    for lk in scored:
        if lk["score"] < FOLLOW_THRESHOLD:
            continue

        ckey = _canonical(lk["url"])
        prev = results.get(ckey)
        if prev is None or lk["score"] > prev["score"]:
            results[ckey] = {
                "url": _clean_display(lk["url"]),
                "score": lk["score"],
                "parent": url,
                "anchor": lk["anchor"],
            }

        if depth < MAX_DEPTH and ckey not in visited and state["fetched"] < MAX_PAGES:
            _crawl(lk["url"], depth + 1, visited, results, state, app, topics)


# -- Main ----------------------------------------------------------------------

def main() -> None:
    try:
        from firecrawl import V1FirecrawlApp  # type: ignore[import]
    except ImportError:
        print("ERROR: pip install firecrawl-py python-dotenv")
        return

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("ERROR: FIRECRAWL_API_KEY not set")
        return

    print(f"\nGUIDED CRAWL -> relevant URL list")
    print(f"  seed       : {URL}")
    print(f"  max depth  : {MAX_DEPTH}   page cap: {MAX_PAGES}")
    print(f"  threshold  : {FOLLOW_THRESHOLD} (raw cosine)")
    print(f"  ollama     : {OLLAMA_HOST} ({OLLAMA_EMBED_MODEL})")
    print(f"  topics     : {len(TOPICS)}\n")

    try:
        _embed_batch([QUERY_PREFIX + TOPICS[0]])  # reachability check
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"ERROR: cannot reach Ollama at {OLLAMA_HOST} -- {exc}")
        return

    app = V1FirecrawlApp(api_key=api_key)
    visited: set[str] = set()
    results: dict[str, dict] = {}
    state = {"fetched": 0, "fetch_failed": 0}

    results[_canonical(URL)] = {
        "url": _clean_display(URL), "score": 1.0,
        "parent": "(seed)", "anchor": "(seed)",
    }

    t0 = time.time()
    _crawl(URL, 0, visited, results, state, app, TOPICS)
    elapsed = time.time() - t0

    ranked = sorted(results.values(), key=lambda x: x["score"], reverse=True)

    print("=" * 72)
    print(f"RELEVANT PAGES FOUND: {len(ranked)}")
    print(f"(fetched {state['fetched']} pages, {state['fetch_failed']} fetch failures, "
          f"{elapsed:.1f}s"
          + ("  [HIT PAGE CAP]" if state["fetched"] >= MAX_PAGES else "")
          + ")\n")

    for r in ranked:
        print(f"  [{r['score']:.3f}]  {r['url']}")
        print(f"           via '{r['anchor'][:50]}'  <- {urlparse(r['parent']).path or '(seed)'}")

    print("\n" + "=" * 72)
    print("URLS ONLY:")
    for r in ranked:
        print(r["url"])
    print()


if __name__ == "__main__":
    main()
