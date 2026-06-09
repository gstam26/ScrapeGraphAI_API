"""
Acquire-layer diagnostic.

Runs only the acquire layer on an Excel input file and prints a clear
per-page table, then saves a report Excel so you can compare fetch backends
and crawl quality without running the full pipeline.

Usage:
    python diagnostics/acquire_report.py samples/input1.xlsx
    python diagnostics/acquire_report.py samples/input1.xlsx --output outputs/acquire_report.xlsx
    python diagnostics/acquire_report.py samples/input1.xlsx --backend firecrawl
    python diagnostics/acquire_report.py samples/input1.xlsx --no-crawl
"""

import argparse
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from config import (
    ACQUIRE_TOOL,
    CACHE_DIR,
    CRAWL_MAX_PAGES,
    CRAWL_MIN_SCORE,
    CRAWL_MIN_SCORE_EMBED,
    DEFAULT_DEPTH,
    REQUEST_HEADERS,
)
from io_excel import read_input
from models import Config
from src.acquire import FetchedPage, acquire


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status_icon(status: str) -> str:
    return {"ok": "✓", "cached": "~", "gate_failed": "!", "error": "✗"}.get(status, "?")


def _bar(n: int, total: int, width: int = 20) -> str:
    filled = int(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _preview(text: str, chars: int = 120) -> str:
    t = text.strip().replace("\n", " ")
    return t[:chars] + "…" if len(t) > chars else t


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Acquire-layer diagnostic")
    parser.add_argument("input", help="Path to the input Excel file")
    parser.add_argument("--output", default="", help="Path to save the report Excel (optional)")
    parser.add_argument("--backend", default="", help="Override ACQUIRE_TOOL (local/firecrawl/playwright/requests)")
    parser.add_argument("--max-pages", type=int, default=0, help="Override CRAWL_MAX_PAGES")
    parser.add_argument("--no-crawl", action="store_true", help="Force depth=0 for all URLs (no crawling)")
    args = parser.parse_args()

    pipeline_input = read_input(args.input)

    cfg = Config(
        acquire_tool=args.backend or ACQUIRE_TOOL,
        cache_dir=CACHE_DIR,
        request_headers=REQUEST_HEADERS,
        default_depth=DEFAULT_DEPTH,
        crawl_min_score=CRAWL_MIN_SCORE,
        crawl_min_score_embed=CRAWL_MIN_SCORE_EMBED,
        crawl_max_pages=args.max_pages or CRAWL_MAX_PAGES,
    )

    print(f"\n{'='*72}")
    print(f"  ACQUIRE DIAGNOSTIC")
    print(f"  input    : {args.input}")
    print(f"  backend  : {cfg.acquire_tool}")
    print(f"  entities : {', '.join(pipeline_input.entities)}")
    print(f"  urls     : {len(pipeline_input.urls)}")
    print(f"  max pages: {cfg.crawl_max_pages}")
    print(f"{'='*72}\n")

    diag: dict = {}
    t0 = time.time()

    url_tuples = []
    for spec in pipeline_input.urls:
        depth = 0 if args.no_crawl else spec.depth
        url_tuples.append((spec.url, depth))

    pages: list[FetchedPage] = acquire(
        url_tuples,
        cfg,
        columns=pipeline_input.columns,
        entities=pipeline_input.entities,
        diag=diag,
    )

    elapsed = time.time() - t0

    # ── Per-page table ────────────────────────────────────────────────────────

    print(f"\n{'─'*72}")
    print(f"  {'ST':<3} {'BACKEND':<16} {'D':<2} {'SCORE':<6} {'CHARS':<7} {'MS':<6}  URL")
    print(f"{'─'*72}")

    rows = []
    for p in pages:
        icon = _status_icon(p.status)
        score = f"{p.crawl_score:.2f}" if p.depth > 0 else "seed"
        chars = len(p.markdown) if p.markdown else 0
        gate = "" if p.gate_passed is not False else f"  [{p.gate_reason}]"
        print(f"  {icon:<3} {p.backend:<16} {p.depth:<2} {score:<6} {chars:<7} {p.fetch_time_ms:<6}  {p.url}{gate}")

        rows.append({
            "status": p.status,
            "backend": p.backend,
            "depth": p.depth,
            "crawl_score": round(p.crawl_score, 3),
            "chars": chars,
            "fetch_time_ms": p.fetch_time_ms,
            "gate_passed": p.gate_passed,
            "gate_reason": p.gate_reason,
            "render_fallback": p.render_fallback,
            "url": p.url,
            "content_preview": _preview(p.markdown or ""),
        })

    print(f"{'─'*72}\n")

    # ── Summary ───────────────────────────────────────────────────────────────

    total = len(pages)
    by_status = {}
    for p in pages:
        by_status[p.status] = by_status.get(p.status, 0) + 1
    by_backend = {}
    for p in pages:
        by_backend[p.backend] = by_backend.get(p.backend, 0) + 1

    print(f"  SUMMARY  ({total} pages in {elapsed:.1f}s)")
    print()
    for status, count in sorted(by_status.items()):
        icon = _status_icon(status)
        print(f"    {icon} {status:<14} {count:>3}  {_bar(count, total)}")
    print()
    print(f"  Backends used:")
    for backend, count in sorted(by_backend.items(), key=lambda x: -x[1]):
        print(f"    {backend:<18} {count:>3} pages")

    chars_ok = [len(p.markdown) for p in pages if p.markdown and p.status in ("ok", "cached")]
    if chars_ok:
        avg = sum(chars_ok) / len(chars_ok)
        print(f"\n  Avg content (ok/cached): {avg:,.0f} chars  "
              f"min={min(chars_ok):,}  max={max(chars_ok):,}")

    print()

    # ── Save report ───────────────────────────────────────────────────────────

    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output_path = os.path.join("outputs", f"{base}_acquire_report.xlsx")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df_pages = pd.DataFrame(rows)

    crawl_rows = diag.get("crawl_candidates", [])
    df_crawl = pd.DataFrame(crawl_rows) if crawl_rows else pd.DataFrame(
        columns=["parent_url", "candidate_url", "anchor_text", "crawl_score", "threshold", "followed", "skip_reason"]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_pages.to_excel(writer, sheet_name="Pages", index=False)
        df_crawl.to_excel(writer, sheet_name="Crawl Candidates", index=False)

    print(f"  Report saved → {output_path}\n")


if __name__ == "__main__":
    main()
