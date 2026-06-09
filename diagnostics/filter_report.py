"""
Filter-layer diagnostic.

Fetches pages using the Acquire layer, then runs the Filter layer and shows
per-page routing decisions with per-column cosine scores so you can inspect
why each column was included or excluded.

Note: src/filter.py exposes filter_page() per-page only. Scores are computed
here by calling embed_batch() directly, mirroring filter.py's logic exactly.

Usage:
    python diagnostics/filter_report.py samples/input1.xlsx
    python diagnostics/filter_report.py samples/input1.xlsx --backend local
    python diagnostics/filter_report.py samples/input1.xlsx --no-crawl
    python diagnostics/filter_report.py samples/input1.xlsx --output outputs/filter.xlsx

Requires:
    Reachable Ollama host (internal network / VPN)
"""

import argparse
import math
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
    FILTER_THRESHOLD,
    OLLAMA_DOC_PREFIX,
    OLLAMA_QUERY_PREFIX,
    REQUEST_HEADERS,
)
from src.io_excel import read_input
from models import Config
from src.acquire import FetchedPage, acquire


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _bar(n: int, total: int, width: int = 20) -> str:
    filled = int(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _filter_with_scores(
    pages: list[FetchedPage],
    columns: list,
    threshold: float,
) -> list[dict]:
    """
    Embed all questions once, then score each page against every question.
    Returns per-page dicts with routing decision and per-column cosine scores.
    Raises on Ollama failure — caller wraps in try/except.
    """
    from src.embed import embed_batch

    all_names = [col.name for col in columns]
    if not all_names:
        return []

    question_texts = [OLLAMA_QUERY_PREFIX + name for name in all_names]
    q_embs = embed_batch(question_texts)

    results = []
    for p in pages:
        text = (p.markdown or "")[:2000]
        if not text:
            results.append({
                "url": p.url,
                "depth": p.depth,
                "scores": {name: None for name in all_names},
                "relevant": set(all_names),
                "fallback": True,
            })
            continue

        page_emb = embed_batch([OLLAMA_DOC_PREFIX + text])[0]
        scores = {
            name: round(_cosine(page_emb, qv), 3)
            for name, qv in zip(all_names, q_embs)
        }
        relevant = {name for name, s in scores.items() if s >= threshold}
        fallback = not relevant
        if fallback:
            relevant = set(all_names)

        results.append({
            "url": p.url,
            "depth": p.depth,
            "scores": scores,
            "relevant": relevant,
            "fallback": fallback,
        })

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Filter-layer diagnostic")
    parser.add_argument("input", help="Path to the input Excel file")
    parser.add_argument("--output", default="", help="Path to save the report Excel (optional)")
    parser.add_argument("--backend", default="firecrawl", help="Acquire backend (default: firecrawl)")
    parser.add_argument("--max-pages", type=int, default=0, help="Override CRAWL_MAX_PAGES")
    parser.add_argument("--no-crawl", action="store_true", help="Force depth=0 for all URLs (no crawling)")
    args = parser.parse_args()

    pipeline_input = read_input(args.input)
    columns = pipeline_input.columns
    total_cols = len(columns)

    cfg = Config(
        acquire_tool=args.backend,
        cache_dir=CACHE_DIR,
        request_headers=REQUEST_HEADERS,
        default_depth=DEFAULT_DEPTH,
        crawl_min_score=CRAWL_MIN_SCORE,
        crawl_min_score_embed=CRAWL_MIN_SCORE_EMBED,
        crawl_max_pages=args.max_pages or CRAWL_MAX_PAGES,
    )

    print(f"\n{'='*72}")
    print(f"  FILTER DIAGNOSTIC")
    print(f"  input     : {args.input}")
    print(f"  backend   : {cfg.acquire_tool}")
    print(f"  entities  : {', '.join(pipeline_input.entities)}")
    print(f"  columns   : {total_cols}")
    print(f"  threshold : {FILTER_THRESHOLD}")
    print(f"{'='*72}\n")

    if not columns:
        print("  No columns in input — filter cannot run.")
        return

    # ── Acquire ───────────────────────────────────────────────────────────────

    url_tuples = []
    for spec in pipeline_input.urls:
        depth = 0 if args.no_crawl else spec.depth
        url_tuples.append((spec.url, depth))

    print("  Acquiring pages...")
    t0 = time.time()
    pages: list[FetchedPage] = acquire(
        url_tuples,
        cfg,
        columns=columns,
        entities=pipeline_input.entities,
    )
    elapsed_acquire = time.time() - t0
    print(f"  {len(pages)} page(s) acquired in {elapsed_acquire:.1f}s\n")

    if not pages:
        print("  No pages acquired — nothing to filter.")
        return

    # ── Filter ────────────────────────────────────────────────────────────────

    print("  Scoring pages against questions (Ollama required)...")
    try:
        t1 = time.time()
        filter_results = _filter_with_scores(pages, columns, FILTER_THRESHOLD)
        elapsed_filter = time.time() - t1
        print(f"  {len(filter_results)} page(s) scored in {elapsed_filter:.1f}s\n")
    except Exception as exc:
        print(f"\n  ERROR: Ollama not reachable — cannot score pages.")
        print(f"  {exc}")
        return

    # ── Per-page table ────────────────────────────────────────────────────────

    print(f"{'─'*72}")

    excel_rows = []
    for fr in filter_results:
        n_relevant = len(fr["relevant"])
        fallback_note = "  [fallback: all columns]" if fr["fallback"] else ""
        depth_tag = f"  [depth={fr['depth']}]" if fr["depth"] > 0 else ""
        print(f"  {fr['url']}{depth_tag}")
        print(f"    {n_relevant}/{total_cols} relevant{fallback_note}")
        for name, score in fr["scores"].items():
            tick = "✓" if name in fr["relevant"] else "✗"
            score_str = f"{score:.3f}" if score is not None else "n/a "
            print(f"    {tick} {score_str}  {name}")
        print()

        for name, score in fr["scores"].items():
            excel_rows.append({
                "url": fr["url"],
                "depth": fr["depth"],
                "column": name,
                "score": score,
                "above_threshold": (score is not None and score >= FILTER_THRESHOLD),
                "relevant": name in fr["relevant"],
                "fallback": fr["fallback"],
            })

    print(f"{'─'*72}\n")

    # ── Summary ───────────────────────────────────────────────────────────────

    total_pages = len(filter_results)
    relevant_counts = [len(fr["relevant"]) for fr in filter_results]
    avg_relevant = sum(relevant_counts) / total_pages if total_pages else 0
    all_full = sum(1 for fr in filter_results if len(fr["relevant"]) == total_cols and not fr["fallback"])
    fallback_count = sum(1 for fr in filter_results if fr["fallback"])
    reduced_count = total_pages - all_full - fallback_count

    print(f"  SUMMARY  ({total_pages} pages, threshold={FILTER_THRESHOLD})")
    print()
    print(f"    avg columns marked relevant : {avg_relevant:.1f} / {total_cols}")
    print(f"    all columns relevant        : {all_full:>3}  {_bar(all_full, total_pages)}")
    print(f"    reduced set                 : {reduced_count:>3}  {_bar(reduced_count, total_pages)}")
    print(f"    fallback (none cleared)     : {fallback_count:>3}  {_bar(fallback_count, total_pages)}")
    print()

    # ── Per-column summary ────────────────────────────────────────────────────

    print(f"  PER-COLUMN  (how often each column is relevant across all pages)")
    print()
    for col in columns:
        name = col.name
        count = sum(1 for fr in filter_results if name in fr["relevant"])
        scored = [fr["scores"][name] for fr in filter_results if fr["scores"].get(name) is not None]
        avg_score = sum(scored) / len(scored) if scored else 0.0
        print(f"    {count:>3}/{total_pages}  avg={avg_score:.3f}  {name}")
    print()

    # ── Save report ───────────────────────────────────────────────────────────

    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output_path = os.path.join("outputs", f"{base}_filter_report.xlsx")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df = pd.DataFrame(excel_rows) if excel_rows else pd.DataFrame(
        columns=["url", "depth", "column", "score", "above_threshold", "relevant", "fallback"]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Filter", index=False)

    print(f"  Report saved → {output_path}\n")


if __name__ == "__main__":
    main()
