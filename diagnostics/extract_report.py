"""
Extract-layer diagnostic.

Fetches pages using the Acquire layer, runs Filter to get per-column
routing decisions, then runs Extract on each page restricted to the
columns Filter marked relevant. Shows per-page extraction results with
value previews, quote previews, and null/populated status, plus a
summary of cell coverage and timing.

Usage:
    python diagnostics/extract_report.py samples/test_smoke.xlsx
    python diagnostics/extract_report.py samples/test_smoke.xlsx --backend local
    python diagnostics/extract_report.py samples/test_smoke.xlsx --no-crawl
    python diagnostics/extract_report.py samples/test_smoke.xlsx --output outputs/extract.xlsx

Requires:
    LLM_API_URL in environment (or .env) when EXTRACT_TOOL=llmapi
    Reachable Ollama host for Filter scoring (internal network / VPN)
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
    CACHE_DIR,
    CRAWL_MAX_PAGES,
    CRAWL_MIN_SCORE,
    CRAWL_MIN_SCORE_EMBED,
    DEFAULT_DEPTH,
    EXTRACT_TOOL,
    REQUEST_HEADERS,
)
from src.io_excel import read_input
from models import Config, PageDoc
from src.acquire import FetchedPage, acquire
from src.filter import filter_page
from src.extract import extract_cells


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bar(n: int, total: int, width: int = 20) -> str:
    filled = int(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _trunc(s: str, n: int = 80) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _value_preview(value) -> str:
    if value is None:
        return "(null)"
    if isinstance(value, list):
        first = _trunc(str(value[0]), 60) if value else ""
        return f"[{len(value)} items]  {first}" if first else f"[{len(value)} items]"
    return _trunc(str(value), 80)


def _page_doc(fp: FetchedPage) -> PageDoc:
    return PageDoc(
        url=fp.url,
        text=fp.markdown,
        html=None,
        from_cache=fp.status == "cached",
        depth=fp.depth,
        crawl_score=fp.crawl_score,
        fetch_time_ms=fp.fetch_time_ms,
        backend=fp.backend,
        render_fallback=fp.render_fallback,
        gate_passed=fp.gate_passed,
        gate_reason=fp.gate_reason,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract-layer diagnostic")
    parser.add_argument("input", help="Path to the input Excel file")
    parser.add_argument("--output", default="", help="Path to save the report Excel (optional)")
    parser.add_argument("--backend", default="firecrawl", help="Acquire backend (default: firecrawl)")
    parser.add_argument(
        "--extract-tool", default=EXTRACT_TOOL, dest="extract_tool",
        help=f"Extract tool override (default: {EXTRACT_TOOL})",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="Override CRAWL_MAX_PAGES")
    parser.add_argument("--no-crawl", action="store_true", help="Force depth=0 for all URLs (no crawling)")
    args = parser.parse_args()

    pipeline_input = read_input(args.input)
    columns = pipeline_input.columns
    entities = pipeline_input.entities
    total_cols = len(columns)

    cfg = Config(
        acquire_tool=args.backend,
        extract_tool=args.extract_tool,
        cache_dir=CACHE_DIR,
        request_headers=REQUEST_HEADERS,
        default_depth=DEFAULT_DEPTH,
        crawl_min_score=CRAWL_MIN_SCORE,
        crawl_min_score_embed=CRAWL_MIN_SCORE_EMBED,
        crawl_max_pages=args.max_pages or CRAWL_MAX_PAGES,
    )

    print(f"\n{'='*72}")
    print(f"  EXTRACT DIAGNOSTIC")
    print(f"  input        : {args.input}")
    print(f"  backend      : {cfg.acquire_tool}")
    print(f"  extract tool : {cfg.extract_tool}")
    print(f"  entities     : {', '.join(entities)}")
    print(f"  columns      : {total_cols}")
    print(f"{'='*72}\n")

    if not columns:
        print("  No columns in input — extract cannot run.")
        return
    if not entities:
        print("  No entities in input — extract cannot run.")
        return

    # ── Acquire ───────────────────────────────────────────────────────────────

    url_tuples = []
    for spec in pipeline_input.urls:
        depth = 0 if args.no_crawl else spec.depth
        url_tuples.append((spec.url, depth))

    print("  Acquiring pages...")
    t0 = time.time()
    fetched: list[FetchedPage] = acquire(
        url_tuples,
        cfg,
        columns=columns,
        entities=entities,
    )
    elapsed_acquire = time.time() - t0
    print(f"  {len(fetched)} page(s) acquired in {elapsed_acquire:.1f}s\n")

    if not fetched:
        print("  No pages acquired — nothing to extract.")
        return

    # ── Filter + Extract ──────────────────────────────────────────────────────

    excel_rows: list[dict] = []
    total_attempted = 0
    total_populated = 0
    total_null = 0
    total_skipped = 0
    total_timeouts = 0
    total_errors = 0
    populated_per_page: list[int] = []

    print(f"{'─'*72}")

    for fp in fetched:
        page = _page_doc(fp)
        page_len = len(page.text) if page.text else 0
        depth_tag = f"  [depth={fp.depth}]" if fp.depth > 0 else ""
        cache_tag = "  [cached]" if fp.status == "cached" else ""
        print(f"\n  {fp.url}{depth_tag}{cache_tag}")
        print(f"    {page_len:,} chars  |  entities: {', '.join(entities)}")

        # Filter
        try:
            routed = filter_page(page, columns)
        except Exception as exc:
            print(f"    ! Filter failed: {exc}")
            total_errors += 1
            continue

        relevant_cols = [c for c in columns if c.name in routed.relevant_columns]
        skipped_cols  = [c for c in columns if c.name not in routed.relevant_columns]

        print(f"    filter: {len(relevant_cols)}/{total_cols} columns relevant", end="")
        if skipped_cols:
            print(f"  |  skipped: [{', '.join(c.name for c in skipped_cols)}]", end="")
        print()

        total_skipped += len(skipped_cols) * len(entities)

        # Extract
        local_diag: dict = {}
        t1 = time.time()
        try:
            cells = extract_cells(routed.page, relevant_cols, entities, cfg=cfg, diag=local_diag)
        except Exception as exc:
            print(f"    ! Extraction failed: {exc}")
            total_errors += 1
            continue
        elapsed_extract = time.time() - t1

        # One page = one set of chunk calls shared across entities; count once.
        if any(l.get("timed_out") for l in local_diag.get("extract_log", [])):
            total_timeouts += 1

        cell_map = {(c.entity, c.column): c for c in cells}
        page_populated = 0

        for entity in entities:
            print(f"\n    [{entity}]")
            log_entry = next(
                (l for l in local_diag.get("extract_log", []) if l.get("entity") == entity),
                {},
            )

            for col in relevant_cols:
                cell = cell_map.get((entity, col.name))
                populated = bool(cell and cell.evidence)
                tick = "✓" if populated else "✗"
                print(f"      {tick} {col.name}")
                if populated:
                    print(f"          value : {_value_preview(cell.value)}")
                    if cell.evidence[0].quote:
                        print(f"          quote : {_trunc(cell.evidence[0].quote, 80)}")
                    page_populated += 1
                    total_populated += 1
                else:
                    print(f"          (null)")
                    total_null += 1
                total_attempted += 1

                first_ev = cell.evidence[0] if cell and cell.evidence else None
                excel_rows.append({
                    "url": fp.url,
                    "depth": fp.depth,
                    "entity": entity,
                    "column": col.name,
                    "status": "populated" if populated else "null",
                    "value_preview": _trunc(str(cell.value), 120) if cell and cell.value is not None else "",
                    "quote_preview": _trunc(first_ev.quote, 120) if first_ev and first_ev.quote else "",
                    "page_length": page_len,
                    "extraction_time_ms": log_entry.get("extraction_time_ms", ""),
                    "timed_out": log_entry.get("timed_out", False),
                })

            for col in skipped_cols:
                excel_rows.append({
                    "url": fp.url,
                    "depth": fp.depth,
                    "entity": entity,
                    "column": col.name,
                    "status": "skipped_by_filter",
                    "value_preview": "",
                    "quote_preview": "",
                    "page_length": page_len,
                    "extraction_time_ms": "",
                    "timed_out": False,
                })

        n_attempted_page = len(relevant_cols) * len(entities)
        populated_per_page.append(page_populated)
        print(f"\n    → {page_populated} populated / {n_attempted_page} attempted  ({elapsed_extract:.1f}s)")

    print(f"\n{'─'*72}\n")

    # ── Summary ───────────────────────────────────────────────────────────────

    total_pages = len(fetched)
    avg_populated = sum(populated_per_page) / total_pages if total_pages else 0.0

    print(f"  SUMMARY  ({total_pages} pages, {len(entities)} entity/entities, {total_cols} column(s))")
    print()
    print(f"    cells attempted (filter passed) : {total_attempted:>4}")
    print(f"    populated                       : {total_populated:>4}  {_bar(total_populated, total_attempted)}")
    print(f"    null                            : {total_null:>4}  {_bar(total_null, total_attempted)}")
    print(f"    skipped by filter               : {total_skipped:>4}")
    print(f"    avg populated cells per page    : {avg_populated:.1f}")
    print(f"    timeouts                        : {total_timeouts:>4}")
    print(f"    errors                          : {total_errors:>4}")
    print()

    # ── Save report ───────────────────────────────────────────────────────────

    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output_path = os.path.join("outputs", f"{base}_extract_report.xlsx")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df = pd.DataFrame(excel_rows) if excel_rows else pd.DataFrame(
        columns=["url", "depth", "entity", "column", "status", "value_preview",
                 "quote_preview", "page_length", "extraction_time_ms", "timed_out"]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Extract", index=False)

    print(f"  Report saved → {output_path}\n")


if __name__ == "__main__":
    main()
