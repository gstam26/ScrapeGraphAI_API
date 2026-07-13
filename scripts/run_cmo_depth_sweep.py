"""Run the CMO depth-sweep workbooks (depth 0/1/2, fixed 5-entity sample) and
print the runtime-vs-depth / populated-cells-vs-depth comparison directly —
no manual eyeballing of three separate Matrix sheets.

Requires Azure keys (.env) — laptop only, same as any real pipeline run.

Usage (from repo root, after build_cmo_workbook.py --entities has produced
the three named-depth workbooks):
    python scripts/run_cmo_depth_sweep.py

Runs each cmo-inputs/cmo_input_named_depth{0,1,2}.xlsx in sequence (least
crawling first — cheapest failure mode first), times the pipeline call
directly (not wall-clock on the whole process), writes a full output workbook
per depth to cmo-outputs/, then reads each Matrix back and reports per-question
populated-cell counts. One depth failing (e.g. a transient fetch error) does
not stop the others — each run is wrapped and the summary marks it FAILED
rather than losing the whole sweep. Writes depth_sweep_summary.csv for
plotting.
"""
import os
import sys
import time
import traceback

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.io_excel import read_input, write_output_excel
from pipeline import run_pipeline

IN_DIR = "cmo-inputs"
OUT_DIR = "cmo-outputs"
DEPTHS = [0, 1, 2]
_EMPTY_MARKERS = {"", "no data found"}


def _populated(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    return str(value).strip().lower() not in _EMPTY_MARKERS


def run_one(depth: int) -> dict:
    in_path = os.path.join(IN_DIR, f"cmo_input_named_depth{depth}.xlsx")
    out_path = os.path.join(OUT_DIR, f"cmo_output_depth{depth}.xlsx")
    if not os.path.exists(in_path):
        return {"depth": depth, "status": f"MISSING INPUT: {in_path}"}

    print(f"\n{'=' * 60}\ndepth {depth}: {in_path}\n{'=' * 60}")
    pipeline_input = read_input(in_path)
    # Isolate each depth's page cache. All three depths share the same seed
    # URLs, so without this a later run's crawl hits cache for pages an
    # earlier run already fetched — the deeper crawl reads a stale page set
    # instead of running its own live crawl, and the depths stop being
    # comparable (observed 2026-07-13: depth 1 and depth 2 produced
    # byte-identical page/cell counts because depth 2 inherited depth 1's
    # cache within the same process). A fresh dir per depth costs one extra
    # live fetch of the shared seeds each run; correctness over speed here.
    pipeline_input.config_overrides = {
        **pipeline_input.config_overrides,
        "CACHE_DIR": f"cache_cmo_depth{depth}",
    }

    t0 = time.time()
    try:
        result, diag = run_pipeline(pipeline_input)
    except Exception:
        print(f"!! depth {depth} FAILED:")
        traceback.print_exc()
        return {"depth": depth, "status": "FAILED", "seconds": round(time.time() - t0, 1)}
    elapsed = time.time() - t0

    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        write_output_excel(result, pipeline_input.columns, out_path, diag=diag)
    except PermissionError:
        # Windows: the previous run's workbook is open in Excel, which locks
        # the path. The pipeline work is already done — don't throw it away;
        # write to a timestamped name instead and say so.
        alt = out_path.replace(".xlsx", f"_{time.strftime('%H%M%S')}.xlsx")
        print(f"!! {out_path} is locked (open in Excel?) — writing {alt} instead")
        write_output_excel(result, pipeline_input.columns, alt, diag=diag)
        out_path = alt

    matrix = pd.read_excel(out_path, sheet_name="Matrix").set_index("Entity")
    questions = list(matrix.columns)
    per_q = {q: int(matrix[q].map(_populated).sum()) for q in questions}
    total_cells = len(matrix) * len(questions)
    total_populated = sum(per_q.values())

    pages_fetched = len(diag.get("acquire_log", [])) if diag else None

    print(f"depth {depth}: {elapsed:.1f}s, {pages_fetched} pages fetched, "
          f"{total_populated}/{total_cells} cells populated")

    return {
        "depth": depth, "status": "ok", "seconds": round(elapsed, 1),
        "pages_fetched": pages_fetched, "entities": len(matrix),
        "questions": len(questions), "total_populated": total_populated,
        "total_cells": total_cells,
        **{f"q_{q[:30]}": per_q[q] for q in questions},
    }


def main() -> int:
    rows = [run_one(d) for d in DEPTHS]
    summary = pd.DataFrame(rows)

    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    core_cols = [c for c in ["depth", "status", "seconds", "pages_fetched",
                              "total_populated", "total_cells"] if c in summary.columns]
    print(summary[core_cols].to_string(index=False))

    os.makedirs(OUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUT_DIR, "depth_sweep_summary.csv")
    try:
        summary.to_csv(csv_path, index=False)
    except PermissionError:
        csv_path = csv_path.replace(".csv", f"_{time.strftime('%H%M%S')}.csv")
        print(f"!! summary CSV locked (open in Excel?) — writing {csv_path} instead")
        summary.to_csv(csv_path, index=False)
    print(f"\nFull per-question breakdown written: {csv_path}")
    print("Per-depth workbooks: cmo-outputs/cmo_output_depth{0,1,2}.xlsx")
    return 0 if (summary["status"] == "ok").all() else 1


if __name__ == "__main__":
    raise SystemExit(main())
