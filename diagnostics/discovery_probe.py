"""Link-discovery probe: how many candidates does a seed actually yield?

Answers the acquire-starvation question at the crawler layer alone — no
Azure, no extraction, no scoring. For each seed it runs discovery twice
against the SAME cache dir:

  cold  — seed fetched live (backend pooled_hybrid_static / _render)
  warm  — seed served from cache (backend "cache", html=None)

The warm leg is the point. A cache hit carries no HTML, so discovery falls
back to a live static fetch and a JS-injected nav stays invisible; the first
cut of the render-for-discovery trigger excluded cache-hit seeds, which
silently preserved the blind spot on every warm-cache run (the 2026-07-29
Automatic re-test failed to fire for exactly this reason). If the warm leg
reports fired=False while cold reports fired=True, that regression is back.

Usage:
    python diagnostics/discovery_probe.py https://automatic.com.hk/
    python diagnostics/discovery_probe.py <url> [<url> ...] \
        [--scope site|host] [--no-render] [--cache-dir DIR] [--keep-cache]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _probe(url: str, cfg, crawler, fetcher) -> list[dict]:
    """Run cold then warm discovery for one seed; return a row per leg."""
    rows = []
    for leg in ("cold", "warm"):
        page = crawler._acquire_page_cfg(url, cfg)
        static = crawler._discover_links(
            page_url=url, start_url=url, depth=1,
            html=page.html, page_text=page.text, cfg=cfg,
        )
        # Match crawl_entity: the seed is already in `visited` when discovery
        # runs, so its self-link is not a followable candidate.
        seed_key = crawler._normalise_url(url)
        n_novel = sum(1 for c in static if crawler._normalise_url(c.url) != seed_key)
        fired = crawler._needs_discovery_render(0, page.backend, n_novel)
        merged = n_novel
        rendered_err = ""
        if fired:
            try:
                html = fetcher._render_page_html(url, cfg)
                seen = {c.url for c in static}
                merged += len([
                    c for c in crawler._discover_links_from_html(url, url, 1, html)
                    if c.url not in seen
                ])
            except Exception as exc:  # render is best-effort; static links kept
                rendered_err = str(exc)[:80]
        rows.append({
            "url": url, "leg": leg, "backend": page.backend,
            "chars": len(page.text or ""), "static_links": n_novel,
            "render_fired": fired, "total_links": merged, "error": rendered_err,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Cold/warm link-discovery probe")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--scope", default="site", choices=["site", "host"])
    ap.add_argument("--no-render", action="store_true",
                    help="leave CRAWL_RENDER_FOR_DISCOVERY off (control leg)")
    ap.add_argument("--cache-dir", default="cache_discovery_probe")
    ap.add_argument("--keep-cache", action="store_true",
                    help="reuse/keep the probe cache instead of starting clean")
    ap.add_argument("--max-pages", type=int, default=40)
    args = ap.parse_args()

    # Crawler reads these into module-level constants at import time.
    os.environ["CRAWL_SCOPE"] = args.scope
    os.environ["CRAWL_RENDER_FOR_DISCOVERY"] = "false" if args.no_render else "true"

    from models import Config
    import src.acquire.crawler as crawler
    import src.acquire.fetcher as fetcher

    if not args.keep_cache and os.path.isdir(args.cache_dir):
        shutil.rmtree(args.cache_dir)
    os.makedirs(args.cache_dir, exist_ok=True)

    cfg = Config(
        acquire_tool="playwright_pooled_hybrid",
        cache_dir=args.cache_dir,
        crawl_max_pages=args.max_pages,
    )

    print(f"scope={args.scope}  render_for_discovery={not args.no_render}  "
          f"cache={args.cache_dir}\n")
    rows = []
    for url in args.urls:
        rows += _probe(url, cfg, crawler, fetcher)

    hdr = f"{'seed':<34}{'leg':<6}{'backend':<24}{'chars':>7}{'static':>8}{'fired':>7}{'total':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['url'][:33]:<34}{r['leg']:<6}{str(r['backend'])[:23]:<24}"
              f"{r['chars']:>7}{r['static_links']:>8}"
              f"{str(r['render_fired']):>7}{r['total_links']:>7}"
              + (f"  ! {r['error']}" if r["error"] else ""))

    # The regression this probe exists to catch.
    bad = [r for r in rows if r["leg"] == "warm" and not r["render_fired"]
           and any(o["leg"] == "cold" and o["render_fired"]
                   and o["url"] == r["url"] for o in rows)]
    print()
    if bad:
        for r in bad:
            print(f"REGRESSION: {r['url']} fired cold but NOT warm "
                  f"(backend={r['backend']}) — cache-hit seeds are being skipped.")
        return 1
    print("Warm-cache parity OK (no seed fires cold-only).")

    if not args.keep_cache:
        shutil.rmtree(args.cache_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
