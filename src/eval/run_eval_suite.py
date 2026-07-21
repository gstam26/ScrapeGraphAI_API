"""
Evaluation suite runner — run the pipeline on every task and score it.

One command replaces the interactive `python main.py` loop plus a separate
`generic_eval.py` call per task. For each task directory under tasks/ that
holds both an `input.xlsx` and a `ground_truth.xlsx`, this:

  1. reads the input workbook (read_input),
  2. runs the pipeline (run_pipeline) — same code path as main.py,
  3. writes the output workbook,
  4. scores it against the task's ground truth (generic_eval.evaluate),

then prints a combined cross-task summary and (unless --no-report) writes one
suite_summary.xlsx plus a per-task output + eval workbook under the run dir.

Design choices worth knowing:
  * Tasks are DISCOVERED, not hardcoded — drop a new tasks/<name>/ dir with
    input.xlsx + ground_truth.xlsx and it joins the suite automatically.
  * Fail-soft PER TASK: a network/Azure failure on one task is caught, marked
    ERROR in the summary, and the remaining tasks still run. The suite never
    dies half-way and loses the tasks that did work.
  * The summary layer is turned OFF by default (before config import) — the
    eval reads only the Provenance sheet, so summarising would burn Azure
    calls for nothing. Pass --with-summary to leave it as configured.
  * Scoring uses generic_eval's semantic matching (nomic-embed) — needs Ollama
    reachable, the same dependency the pipeline already has. If Ollama is down,
    generic_eval prints a warning and falls back to lexical-only automatically.

Usage (from repo root, on the machine with keys/VPN):
  python src/eval/run_eval_suite.py
  python src/eval/run_eval_suite.py --tasks task1_digital_foundations,task3_standards_bodies
  python src/eval/run_eval_suite.py --backend local --outdir outputs/eval_local
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

TASKS_DIR = os.path.join(_REPO_ROOT, "tasks")


def discover_tasks(subset: list[str] | None) -> list[tuple[str, str, str]]:
    """Return [(name, input_path, gt_path)] for every task dir that has both
    files. subset (names) filters and orders; missing names are a hard error
    so a typo never silently drops a task."""
    found: dict[str, tuple[str, str, str]] = {}
    for name in sorted(os.listdir(TASKS_DIR)):
        tdir = os.path.join(TASKS_DIR, name)
        if not os.path.isdir(tdir):
            continue
        inp = os.path.join(tdir, "input.xlsx")
        gt = os.path.join(tdir, "ground_truth.xlsx")
        if os.path.isfile(inp) and os.path.isfile(gt):
            found[name] = (name, inp, gt)
    if subset:
        missing = [s for s in subset if s not in found]
        if missing:
            sys.exit(f"--tasks names not found as task dirs: {missing}\n"
                     f"Available: {sorted(found)}")
        return [found[s] for s in subset]
    return list(found.values())


def run_one_task(name, input_path, gt_path, out_dir, backend, verbose):
    """Run + score one task. Returns a result dict; never raises (errors are
    captured in the dict) so the caller's loop survives a bad task."""
    from src.io_excel import read_input, write_output_excel
    from pipeline import run_pipeline
    from src.eval.generic_eval import (
        read_gt, read_pipeline_output, evaluate, print_report, write_report_excel,
    )

    rec: dict = {"task": name, "status": "ok", "error": None,
                 "seconds": 0.0, "overall": None, "per_question": None,
                 "output_path": None, "eval_path": None}
    t0 = time.time()
    try:
        pi = read_input(input_path)
        if backend:
            pi.config_overrides = {**pi.config_overrides, "ACQUIRE_TOOL": backend}
        print(f"\n{'='*68}\n  RUN {name}: {len(pi.entities)} entities, "
              f"{len(pi.urls)} URL(s), {len(pi.columns)} question(s)\n{'='*68}")

        result, diag = run_pipeline(pi)
        out_path = os.path.join(out_dir, f"{name}_output.xlsx")
        write_output_excel(result, pi.columns, out_path, diag=diag)
        rec["output_path"] = out_path

        gt = read_gt(gt_path)
        ai = read_pipeline_output(out_path)
        ev = evaluate(gt, ai)
        print_report(ev, verbose=verbose)
        rec["overall"] = ev.overall
        rec["per_question"] = ev.per_question

        eval_path = os.path.join(out_dir, f"{name}_eval.xlsx")
        write_report_excel(ev, eval_path)
        rec["eval_path"] = eval_path
    except Exception as e:  # noqa: BLE001 — fail-soft per task by design
        rec["status"] = "ERROR"
        rec["error"] = f"{type(e).__name__}: {e}"
        print(f"\n  !! {name} FAILED: {rec['error']}")
        if verbose:
            traceback.print_exc()
    rec["seconds"] = time.time() - t0
    return rec


def print_suite_summary(records: list[dict]) -> None:
    print(f"\n\n{'#'*72}\n  EVAL SUITE SUMMARY\n{'#'*72}")
    # Headline columns are SINGLE-ANSWER (trustworthy). List F1 is shown flagged
    # (its precision is a lower bound — GT lists are non-exhaustive).
    print("  Headline P/R/F1/HALL = single-answer questions (trustworthy).")
    print("  listF1 = list questions (precision is a LOWER BOUND).\n")
    print(f"{'TASK':<26}{'P':>7}{'R':>7}{'F1':>7}{'HALL':>7}{'listF1':>8}{'sec':>6}")
    print("-" * 72)
    for r in records:
        if r["status"] != "ok":
            print(f"  {r['task']:<24}{'ERROR — ' + (r['error'] or '')[:34]:>44}")
            continue
        s = r["overall"]["single"]
        lf1 = r["overall"]["list"]["F1"]
        print(f"  {r['task']:<24}{s['precision']:>7.3f}{s['recall']:>7.3f}"
              f"{s['F1']:>7.3f}{s['hallucination_rate']:>7.3f}{lf1:>8.3f}{r['seconds']:>6.0f}")
    print("-" * 72)
    ok = [r for r in records if r["status"] == "ok"]
    if ok:
        def _micro(block_key):
            tp = sum(r["overall"][block_key]["TP"] for r in ok)
            fn = sum(r["overall"][block_key]["FN"] for r in ok)
            fp = sum(r["overall"][block_key]["FP"] for r in ok)
            rr = tp / (tp + fn) if tp + fn else 1.0
            pp = tp / (tp + fp) if tp + fp else 1.0
            f1 = 2 * pp * rr / (pp + rr) if pp + rr else 0.0
            return pp, rr, f1, fp / max(1, tp + fp), tp, fn, fp
        pp, rr, f1, hall, tp, fn, fp = _micro("single")
        lf = _micro("list")
        print(f"  {'SUITE single-answer':<24}{pp:>7.3f}{rr:>7.3f}{f1:>7.3f}"
              f"{hall:>7.3f}{lf[2]:>8.3f}")
        print(f"  single-answer TP={tp} FN={fn} FP={fp}  |  "
              f"list TP={lf[4]} FN={lf[5]} FP={lf[6]} (P lower-bound)  "
              f"over {len(ok)}/{len(records)} tasks OK")


def write_suite_summary(records: list[dict], path: str) -> None:
    import pandas as pd
    task_rows, pq_rows = [], []
    for r in records:
        if r["status"] != "ok":
            task_rows.append({"task": r["task"], "status": r["status"],
                              "error": r["error"], "seconds": round(r["seconds"], 1)})
            continue
        o = r["overall"]
        s, ls = o["single"], o["list"]
        task_rows.append({
            "task": r["task"], "status": "ok", "error": "",
            "entities": o["entities"], "cells": o["cells"],
            # Single-answer = trustworthy headline.
            "single_P": s["precision"], "single_R": s["recall"],
            "single_F1": s["F1"], "single_HALL": s["hallucination_rate"],
            "single_TP": s["TP"], "single_FN": s["FN"], "single_FP": s["FP"],
            # List precision is a lower bound (non-exhaustive GT).
            "list_P_lowerbound": ls["precision"], "list_R": ls["recall"],
            "list_F1": ls["F1"],
            "combined_F1": o["F1"],
            "seconds": round(r["seconds"], 1),
        })
        for q, m in r["per_question"].items():
            pq_rows.append({"task": r["task"], "question": q, "cells": m["cells"],
                            "TP": m["TP"], "FN": m["FN"], "FP": m["FP"],
                            "precision": m["precision"], "recall": m["recall"],
                            "F1": m["F1"], "hallucination_rate": m["hallucination_rate"]})
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame(task_rows).to_excel(w, sheet_name="Tasks", index=False)
        if pq_rows:
            pd.DataFrame(pq_rows).to_excel(w, sheet_name="PerQuestion", index=False)
    print(f"\nSuite summary written: {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run + score the full task eval suite")
    ap.add_argument("--tasks", default=None,
                    help="comma-separated task dir names to run (default: all discovered)")
    ap.add_argument("--backend", default="",
                    help="override ACQUIRE_TOOL for every task (e.g. local, playwright_pooled_hybrid)")
    ap.add_argument("--outdir", default=None,
                    help="output dir (default outputs/eval_suite_<timestamp>)")
    ap.add_argument("--with-summary", action="store_true",
                    help="leave SUMMARY_ENABLED as configured (default: force off — "
                         "eval reads only Provenance, so summarising wastes Azure calls)")
    ap.add_argument("--verbose", action="store_true",
                    help="print cell-level eval detail and full tracebacks")
    args = ap.parse_args()

    # Must happen BEFORE config is imported (config reads os.getenv at import).
    if not args.with_summary:
        os.environ["SUMMARY_ENABLED"] = "false"

    subset = [s.strip() for s in args.tasks.split(",")] if args.tasks else None
    tasks = discover_tasks(subset)
    if not tasks:
        sys.exit("No runnable tasks (need tasks/<name>/input.xlsx + ground_truth.xlsx).")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.outdir or os.path.join(_REPO_ROOT, "outputs", f"eval_suite_{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"Eval suite: {len(tasks)} task(s) -> {out_dir}")
    if args.backend:
        print(f"Backend override: {args.backend}")

    records = [run_one_task(n, i, g, out_dir, args.backend, args.verbose)
               for n, i, g in tasks]

    print_suite_summary(records)
    write_suite_summary(records, os.path.join(out_dir, "suite_summary.xlsx"))
    # Non-zero exit if any task errored — CI/scripting can gate on it.
    return 1 if any(r["status"] != "ok" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
