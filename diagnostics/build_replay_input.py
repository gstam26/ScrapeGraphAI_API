"""Build a page-set-pinned replay input workbook from a completed run.

STANDING REQUIREMENT (decision-log 2026-07-06 finding): any before/after
validation of a code change must pin the page set. Crawl link discovery and
scoring re-run live on every run, and cache hits discover links from cached
markdown while live fetches use rawHtml — so "same tool, different run" does
NOT select the same pages, and a Matrix diff between two crawled runs
confounds the code change under test with page-set drift.

This tool generalises diagnostics/backend_compare.py's baseline-replay
pattern to the full pipeline: it reads the Acquire Log of a completed
baseline run and emits a standard 4-sheet input workbook whose urls sheet
lists the baseline's exact fetched pages at depth=0. Depth-0 specs take the
pipeline's direct-fetch path (src/acquire/__init__.py, "Direct fetch path")
— no crawling, no discovery, no scoring — so the replayed page set is pinned
by construction. Questions (with instructions) and config are copied
verbatim from the ORIGINAL input workbook, because the output workbook does
not record instructions.

Run the replay like any input:  python main.py  ->  <replay_input.xlsx>

With the baseline's cache present the replay is fully cache-served (zero
fetch credits); without it, pages are re-fetched live but the URL SET is
still pinned — the comparison stays page-set-stable either way.

Politeness note: a replay has one urls-sheet row per page, so
PIPELINE_ENTITY_WORKERS parallelism is per-page rather than per-domain.
Cache-served replays make no requests at all; live playwright_pooled replays
stay polite regardless (its per-domain delay gate is global across threads);
live Firecrawl replays hit Firecrawl's infrastructure at that concurrency,
not the target site.

Usage:
    python diagnostics/build_replay_input.py \
        --baseline adlm-outputs/validation_sample_run_2026-07-03.xlsx \
        --input    adlm-inputs/validation_sample_input.xlsx \
        --out      adlm-inputs/replay_validation_2026-07-03.xlsx
"""
import argparse
import os
import sys
from collections import Counter

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.io_excel import _find_sheet, _parse_entity_list, _read_entities_sheet

# Statuses that mean "this page contributed content to the baseline".
# The crawl path logs contentful pages as "ok"; the direct-fetch path logs
# cache hits as "cached". Everything else (error / gate_failed / empty) is
# excluded — replaying a page that produced nothing only re-produces nothing.
KEEP_STATUSES = ("ok", "cached")

_REQUIRED_COLS = ("Entities", "Page URL", "Status")


def build_replay_input(
    baseline_path: str,
    input_path: str,
    out_path: str,
    keep_statuses: tuple[str, ...] = KEEP_STATUSES,
) -> dict:
    """Write a replay input workbook; return a summary dict.

    baseline_path: a completed run's output workbook (needs its Acquire Log,
                   i.e. the run must have had DIAGNOSTICS=True).
    input_path:    the ORIGINAL input workbook of that run — source of the
                   entities, questions (instructions!) and config sheets.
    out_path:      the replay input workbook to write.
    """
    baseline = pd.ExcelFile(baseline_path)
    try:
        acq_sheet = _find_sheet(baseline, "Acquire Log")
        if acq_sheet is None:
            raise ValueError(
                f"{baseline_path!r} has no Acquire Log sheet — the baseline run "
                "must be produced with DIAGNOSTICS=True to be replayable."
            )
        aq = pd.read_excel(baseline, acq_sheet)
    finally:
        baseline.close()

    missing = [c for c in _REQUIRED_COLS if c not in aq.columns]
    if missing:
        raise ValueError(f"Acquire Log is missing column(s): {missing}")

    kept: list[tuple[str, str]] = []  # (page_url, entities_cell)
    skipped: Counter = Counter()
    seen: set[tuple[str, str]] = set()
    for _, row in aq.iterrows():
        url = str(row["Page URL"]).strip() if pd.notna(row["Page URL"]) else ""
        entities_cell = str(row["Entities"]).strip() if pd.notna(row["Entities"]) else ""
        status = str(row["Status"]).strip().lower() if pd.notna(row["Status"]) else ""
        if not url:
            continue
        if status not in keep_statuses:
            skipped[status or "(blank)"] += 1
            continue
        key = (entities_cell, url)
        if key in seen:
            continue
        seen.add(key)
        kept.append((url, entities_cell))

    if not kept:
        raise ValueError(
            f"No Acquire Log rows with status in {keep_statuses} — nothing to replay."
        )

    src = pd.ExcelFile(input_path)
    try:
        entities_sheet = _find_sheet(src, "entities")
        if entities_sheet is None:
            raise ValueError(
                f"{input_path!r} has no entities sheet — pass the original "
                "input workbook the baseline run was built from."
            )
        known_entities = _read_entities_sheet(src, entities_sheet)
        entities_df = pd.read_excel(src, entities_sheet)

        questions_sheet = _find_sheet(src, "questions")
        questions_df = pd.read_excel(src, questions_sheet) if questions_sheet else None
        config_sheet = _find_sheet(src, "config")
        config_df = pd.read_excel(src, config_sheet) if config_sheet else None
    finally:
        src.close()

    # Guard: every entity the replayed urls reference must exist in the input's
    # entities sheet, or read_input() will reject the workbook later with a less
    # helpful message. A mismatch usually means the wrong --input was passed —
    # or entity names containing commas, which cannot round-trip through the
    # comma-separated Entities cell (the ADLM builders comma-strip names for
    # exactly this reason).
    known = set(known_entities)
    unknown = sorted({
        name
        for _, cell in kept
        for name in _parse_entity_list(cell)
        if name not in known
    })
    if unknown:
        raise ValueError(
            f"Baseline Acquire Log references entities not in {input_path!r}'s "
            f"entities sheet: {unknown}. Wrong --input workbook, or entity "
            "names containing commas (not round-trippable)."
        )

    referenced = {name for _, cell in kept for name in _parse_entity_list(cell)}
    entities_without_pages = sorted(known - referenced)

    urls_df = pd.DataFrame({
        "url": [u for u, _ in kept],
        "depth": [0] * len(kept),
        "entities": [e for _, e in kept],
    })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        entities_df.to_excel(w, sheet_name="entities", index=False)
        urls_df.to_excel(w, sheet_name="urls", index=False)
        if questions_df is not None:
            questions_df.to_excel(w, sheet_name="questions", index=False)
        if config_df is not None:
            config_df.to_excel(w, sheet_name="config", index=False)

    summary = {
        "pages": len(kept),
        "entities_with_pages": len(referenced),
        "entities_without_pages": entities_without_pages,
        "skipped_by_status": dict(skipped),
        "out_path": out_path,
    }
    print(
        f"Wrote {out_path}: {summary['pages']} pinned pages across "
        f"{summary['entities_with_pages']} entities, all depth=0 (no crawl)."
    )
    if skipped:
        print(f"  Skipped non-content baseline rows: {dict(skipped)}")
    if entities_without_pages:
        print(
            f"  Entities with no replayable pages (will show 'No data found'): "
            f"{entities_without_pages}"
        )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pin a baseline run's page set into a replay input workbook"
    )
    ap.add_argument("--baseline", required=True, help="completed run output .xlsx (with Acquire Log)")
    ap.add_argument("--input", required=True, help="the baseline run's ORIGINAL input .xlsx")
    ap.add_argument("--out", required=True, help="replay input .xlsx to write")
    args = ap.parse_args()
    build_replay_input(args.baseline, args.input, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
