"""
Batch acquire-score evaluation.

Runs diagnostics/acquire_report.py for multiple input workbooks, then
consolidates the resulting crawl-score usefulness diagnostics into a single
summary workbook.

Usage:
    python diagnostics/acquire_batch_eval.py samples/*.xlsx
    python diagnostics/acquire_batch_eval.py samples/*.xlsx --backend firecrawl
    python diagnostics/acquire_batch_eval.py samples/*.xlsx --output outputs/acquire_batch_eval.xlsx
    python diagnostics/acquire_batch_eval.py samples/*.xlsx --skip-existing
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ACQUIRE_REPORT = _REPO_ROOT / "diagnostics" / "acquire_report.py"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values: list[Any]) -> float | None:
    nums = [n for n in (_number(value) for value in values) if n is not None]
    return sum(nums) / len(nums) if nums else None


def _safe_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet_name)
    except ValueError:
        return pd.DataFrame()


def _metric_value(df: pd.DataFrame, metric: str) -> Any:
    if df.empty or "metric" not in df.columns or "value" not in df.columns:
        return ""
    rows = df[df["metric"] == metric]
    if rows.empty:
        return ""
    return rows.iloc[0]["value"]


def _expand_inputs(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        matches = glob.glob(pattern)
        if not matches:
            matches = [pattern]
        for match in matches:
            path = Path(match).resolve()
            if path.is_file() and path.suffix.lower() in {".xlsx", ".xls"} and path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def _report_path(input_path: Path, reports_dir: Path) -> Path:
    return reports_dir / f"{input_path.stem}_acquire_report.xlsx"


def _tail(text: str, max_lines: int = 30, max_chars: int = 6000) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    tail = "\n".join(lines[-max_lines:])
    return tail[-max_chars:]


def _run_acquire_report(
    input_path: Path,
    output_path: Path,
    backend: str,
    scorer: str,
    max_pages: int,
    no_crawl: bool,
    no_extract_cache: bool,
    skip_existing: bool,
) -> dict:
    log_path = output_path.with_suffix(".log")
    if skip_existing and output_path.exists():
        return {
            "input": str(input_path),
            "report": str(output_path),
            "log": str(log_path),
            "command": "",
            "status": "reused",
            "returncode": 0,
            "error": "",
        }

    cmd = [
        sys.executable,
        str(_ACQUIRE_REPORT),
        str(input_path),
        "--output",
        str(output_path),
    ]
    if backend:
        cmd.extend(["--backend", backend])
    if scorer:
        cmd.extend(["--scorer", scorer])
    if max_pages:
        cmd.extend(["--max-pages", str(max_pages)])
    if no_crawl:
        cmd.append("--no-crawl")
    if no_extract_cache:
        cmd.append("--no-extract-cache")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_lines: list[str] = []
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=_REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log_file.write(line)
            output_lines.append(line)
        returncode = proc.wait()

    combined = "".join(output_lines)
    error_tail = _tail(combined) if returncode else ""
    return {
        "input": str(input_path),
        "report": str(output_path),
        "log": str(log_path),
        "command": " ".join(cmd),
        "status": "ok" if returncode == 0 else "failed",
        "returncode": returncode,
        "error": error_tail,
    }


def _workbook_summary(input_path: Path, report_path: Path, run_status: str) -> dict:
    evaluation = _safe_sheet(report_path, "Crawl Score Evaluation")
    usefulness = _safe_sheet(report_path, "Page Usefulness")
    false_pos = _safe_sheet(report_path, "False Positives")
    false_neg = _safe_sheet(report_path, "False Negatives")

    total_pages = int(_number(_metric_value(evaluation, "total_pages_analysed")) or len(usefulness))
    high_score_low = int(_number(_metric_value(evaluation, "high_score_zero_or_low_contribution_pages")) or len(false_pos))
    low_score_high = int(_number(_metric_value(evaluation, "low_score_high_contribution_pages")) or len(false_neg))

    return {
        "input_workbook": str(input_path),
        "report_path": str(report_path),
        "run_status": run_status,
        "total_pages_analysed": total_pages,
        "average_crawl_score": _number(_metric_value(evaluation, "average_crawl_score")),
        "average_contributions": _number(_metric_value(evaluation, "average_contributions")),
        "strongest_positive_correlation": _metric_value(evaluation, "strongest_positive_correlation"),
        "weakest_correlation": _metric_value(evaluation, "weakest_correlation"),
        "high_score_zero_or_low_contribution_pages": high_score_low,
        "low_score_high_contribution_pages": low_score_high,
        "false_positive_rate": round(high_score_low / total_pages, 4) if total_pages else "",
        "false_negative_rate": round(low_score_high / total_pages, 4) if total_pages else "",
        "total_extracted_candidates": usefulness["extracted_candidates"].sum() if "extracted_candidates" in usefulness else 0,
        "total_verified_facts": usefulness["verified_facts"].sum() if "verified_facts" in usefulness else 0,
        "total_final_contributions": usefulness["final_matrix_contributions"].sum() if "final_matrix_contributions" in usefulness else 0,
        "average_contribution_rate": _avg(usefulness["contribution_rate"].tolist()) if "contribution_rate" in usefulness else None,
        "interpretation": _metric_value(evaluation, "interpretation"),
    }


def _entity_summary(input_path: Path, report_path: Path, workbook_summary: dict) -> list[dict]:
    contrib = _safe_sheet(report_path, "Contribution Details")
    usefulness = _safe_sheet(report_path, "Page Usefulness")

    if contrib.empty or "entity" not in contrib.columns:
        return [{
            "input_workbook": str(input_path),
            "entity": "",
            "source_pages_with_contributions": 0,
            "final_contributions": 0,
            "verified_contributions": 0,
            "unique_questions_contributed": 0,
            "avg_verification_score": "",
            "workbook_total_pages": workbook_summary.get("total_pages_analysed", 0),
            "workbook_false_positive_rate": workbook_summary.get("false_positive_rate", ""),
            "workbook_false_negative_rate": workbook_summary.get("false_negative_rate", ""),
            "workbook_average_crawl_score": workbook_summary.get("average_crawl_score", ""),
        }]

    rows: list[dict] = []
    for entity, group in contrib.groupby("entity", dropna=False):
        verified = group["verified"].fillna(False).astype(bool) if "verified" in group else []
        source_pages = group["url"].dropna().nunique() if "url" in group else 0
        questions = group["question"].dropna().nunique() if "question" in group else 0
        avg_score = _avg(group["verification_score"].tolist()) if "verification_score" in group else None
        page_rows = usefulness[usefulness["url"].isin(group["url"].dropna().unique())] if "url" in usefulness else pd.DataFrame()
        rows.append({
            "input_workbook": str(input_path),
            "entity": entity,
            "source_pages_with_contributions": source_pages,
            "final_contributions": len(group),
            "verified_contributions": int(verified.sum()) if hasattr(verified, "sum") else 0,
            "unique_questions_contributed": questions,
            "avg_verification_score": round(avg_score, 3) if avg_score is not None else "",
            "avg_source_crawl_score": round(_avg(page_rows["crawl_score"].tolist()), 3)
            if not page_rows.empty and "crawl_score" in page_rows else "",
            "avg_source_contribution_rate": round(_avg(page_rows["contribution_rate"].tolist()), 3)
            if not page_rows.empty and "contribution_rate" in page_rows else "",
            "workbook_total_pages": workbook_summary.get("total_pages_analysed", 0),
            "workbook_false_positive_rate": workbook_summary.get("false_positive_rate", ""),
            "workbook_false_negative_rate": workbook_summary.get("false_negative_rate", ""),
            "workbook_average_crawl_score": workbook_summary.get("average_crawl_score", ""),
        })
    return rows


def _tagged_sheet(input_path: Path, report_path: Path, sheet_name: str) -> pd.DataFrame:
    df = _safe_sheet(report_path, sheet_name)
    if df.empty:
        return df
    df.insert(0, "input_workbook", str(input_path))
    df.insert(1, "report_path", str(report_path))
    return df


def _write_batch_output(
    output_path: Path,
    run_rows: list[dict],
    workbook_rows: list[dict],
    entity_rows: list[dict],
    correlation_frames: list[pd.DataFrame],
    calibration_frames: list[pd.DataFrame],
    false_positive_frames: list[pd.DataFrame],
    false_negative_frames: list[pd.DataFrame],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pd.DataFrame(workbook_rows).to_excel(writer, sheet_name="Workbook Summary", index=False)
        pd.DataFrame(entity_rows).to_excel(writer, sheet_name="Entity Summary", index=False)
        pd.DataFrame(run_rows).to_excel(writer, sheet_name="Report Runs", index=False)
        pd.concat(correlation_frames, ignore_index=True).to_excel(
            writer, sheet_name="Correlations", index=False
        ) if correlation_frames else pd.DataFrame().to_excel(writer, sheet_name="Correlations", index=False)
        pd.concat(calibration_frames, ignore_index=True).to_excel(
            writer, sheet_name="Calibration", index=False
        ) if calibration_frames else pd.DataFrame().to_excel(writer, sheet_name="Calibration", index=False)
        pd.concat(false_positive_frames, ignore_index=True).to_excel(
            writer, sheet_name="False Positives", index=False
        ) if false_positive_frames else pd.DataFrame().to_excel(writer, sheet_name="False Positives", index=False)
        pd.concat(false_negative_frames, ignore_index=True).to_excel(
            writer, sheet_name="False Negatives", index=False
        ) if false_negative_frames else pd.DataFrame().to_excel(writer, sheet_name="False Negatives", index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch acquire-score evaluation")
    parser.add_argument("inputs", nargs="+", help="Input workbook paths or glob patterns")
    parser.add_argument("--output", default="outputs/acquire_batch_eval.xlsx", help="Summary workbook path")
    parser.add_argument("--reports-dir", default="outputs/acquire_batch_reports", help="Per-workbook report directory")
    parser.add_argument("--backend", default="", help="Forwarded acquire backend override")
    parser.add_argument(
        "--scorer",
        choices=["baseline", "experimental"],
        default="",
        help="Forwarded crawl scoring mode override",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="Forwarded CRAWL_MAX_PAGES override")
    parser.add_argument("--no-crawl", action="store_true", help="Forward --no-crawl to acquire_report.py")
    parser.add_argument("--no-extract-cache", action="store_true", help="Forward --no-extract-cache")
    parser.add_argument("--skip-existing", action="store_true", help="Reuse existing per-workbook acquire reports")
    args = parser.parse_args()

    input_paths = _expand_inputs(args.inputs)
    if not input_paths:
        print("No input workbooks found.")
        return 1

    reports_dir = Path(args.reports_dir).resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output).resolve()

    run_rows: list[dict] = []
    workbook_rows: list[dict] = []
    entity_rows: list[dict] = []
    correlation_frames: list[pd.DataFrame] = []
    calibration_frames: list[pd.DataFrame] = []
    false_positive_frames: list[pd.DataFrame] = []
    false_negative_frames: list[pd.DataFrame] = []

    for input_path in input_paths:
        report_path = _report_path(input_path, reports_dir)
        print(f"\n=== {input_path.name} ===")
        run_row = _run_acquire_report(
            input_path,
            report_path,
            args.backend,
            args.scorer,
            args.max_pages,
            args.no_crawl,
            args.no_extract_cache,
            args.skip_existing,
        )
        run_rows.append(run_row)
        if run_row["status"] == "failed":
            print(f"  failed with return code {run_row['returncode']}")
            if run_row.get("log"):
                print(f"  log: {run_row['log']}")
            if run_row.get("error"):
                print("  traceback tail:")
                for line in run_row["error"].splitlines()[-12:]:
                    print(f"    {line}")
            continue

        summary = _workbook_summary(input_path, report_path, run_row["status"])
        workbook_rows.append(summary)
        entity_rows.extend(_entity_summary(input_path, report_path, summary))

        for sheet_name, frames in (
            ("Score Correlations", correlation_frames),
            ("Score Calibration", calibration_frames),
            ("False Positives", false_positive_frames),
            ("False Negatives", false_negative_frames),
        ):
            df = _tagged_sheet(input_path, report_path, sheet_name)
            if not df.empty:
                frames.append(df)

        print(
            f"  pages={summary['total_pages_analysed']} "
            f"contrib={summary['total_final_contributions']} "
            f"fp_rate={summary['false_positive_rate']} "
            f"fn_rate={summary['false_negative_rate']}"
        )

    _write_batch_output(
        output_path,
        run_rows,
        workbook_rows,
        entity_rows,
        correlation_frames,
        calibration_frames,
        false_positive_frames,
        false_negative_frames,
    )
    print(f"\nBatch evaluation written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
