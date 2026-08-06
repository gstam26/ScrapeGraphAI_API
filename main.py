"""CLI entry point for the entity-extraction pipeline.

Reads an .xlsx input workbook (entities, seed URLs, questions, optional
config sheet), runs the full pipeline (Acquire → Filter → Extract → Verify →
Aggregate, plus optional Grouping/Summary layers), and writes a multi-sheet
.xlsx report under outputs/. Prompts interactively for any path not given as
a flag; --backend overrides the acquire tool for one run. Ends with an
honest run summary that names sites which yielded nothing.
"""
import argparse
import os
import time

from config import OUTPUT_DIR
from src.io_excel import read_input, write_output_excel
from pipeline import run_pipeline


def format_elapsed(seconds: int) -> str:
    d, remainder = divmod(seconds, 86400)
    h, remainder = divmod(remainder, 3600)
    m, s = divmod(remainder, 60)

    parts = []

    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")

    parts.append(f"{s}s")

    return " ".join(parts)


def print_run_summary(result, pipeline_input, diag: dict, elapsed: int) -> None:
    """One honest paragraph at the end of every run.

    Users trust a tool that reports its own limits: alongside the successes,
    this names the sites that yielded nothing — an unreachable or bot-walled
    site is a *finding* about that company, not a silent gap in the output.
    """
    acquire = diag.get("acquire_log", [])
    ok_pages = sum(1 for e in acquire if e.get("status") == "ok")
    failed = [e for e in acquire if e.get("status") in ("gate_failed", "error")]

    # Entities whose crawl produced no usable page at all.
    ok_by_seed: dict[str, int] = {}
    for e in acquire:
        seed = str(e.get("entity_url", ""))
        ok_by_seed.setdefault(seed, 0)
        if e.get("status") == "ok":
            ok_by_seed[seed] += 1
    dead_seeds = sorted(s for s, n in ok_by_seed.items() if n == 0)

    # LLM extraction calls: cache hits are logged with 0ms and cost nothing.
    extract = diag.get("extract_log", [])
    llm_calls = sum(1 for e in extract if e.get("extraction_time_ms", 0) > 0)
    cached = len(extract) - llm_calls

    n_questions = len(pipeline_input.columns)
    n_entities = len(result.rows)
    total_cells = n_entities * n_questions if n_questions else 0
    populated = sum(
        1 for row in result.rows for c in row.cells
        if c.value is not None and str(c.value).strip()
    )

    print("\n" + "=" * 60)
    print(" RUN SUMMARY")
    print("=" * 60)
    print(f"  Entities:        {n_entities}  ({n_questions} questions each)")
    print(f"  Pages fetched:   {ok_pages} usable"
          + (f", {len(failed)} failed/low-quality" if failed else ""))
    print(f"  LLM calls:       {llm_calls}"
          + (f"  (+{cached} served from cache)" if cached else ""))
    if total_cells:
        print(f"  Cells answered:  {populated}/{total_cells} "
              f"({populated / total_cells:.0%})")
    print(f"  Runtime:         {format_elapsed(elapsed)}")
    if dead_seeds:
        print(f"  No content from {len(dead_seeds)} site(s) — unreachable, "
              f"blocked, or empty (a finding, not an error):")
        for s in dead_seeds:
            print(f"    - {s}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Entity extraction pipeline")
    parser.add_argument("--input", default="", metavar="XLSX",
                        help="Path to the input workbook. Omit to be prompted.")
    parser.add_argument("--output", default="", metavar="NAME",
                        help="Output filename (written under outputs/). "
                             "Omit to be prompted.")
    parser.add_argument("--backend", default="", help="Override ACQUIRE_TOOL (playwright_pooled_hybrid/playwright_pooled/firecrawl/local/playwright/requests)")
    args = parser.parse_args()

    print("=== Entity Extraction Pipeline ===\n")

    input_path = args.input.strip() or input("Path to input Excel file: ").strip()
    if not os.path.exists(input_path):
        raise SystemExit(
            f"Input workbook not found: {input_path!r}\n"
            f"Give the path to a .xlsx input workbook (see docs/QUICKSTART.md)."
        )
    pipeline_input = read_input(input_path)
    if args.backend:
        pipeline_input.config_overrides = {
            **pipeline_input.config_overrides,
            "ACQUIRE_TOOL": args.backend,
        }

    print(
        f"\nLoaded {len(pipeline_input.entities)} entity/entities, "
        f"{len(pipeline_input.urls)} URL(s), and "
        f"{len(pipeline_input.columns)} question(s) from '{input_path}'"
    )
    if args.backend:
        print(f"Acquire backend override: {args.backend}")

    if not pipeline_input.urls:
        print("No URLs found. Exiting.")
        return

    if not pipeline_input.columns:
        print("No questions found. Exiting.")
        return

    filename = args.output.strip() or input("\nOutput Excel filename: ").strip()

    if not filename.endswith(".xlsx"):
        filename += ".xlsx"

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)

    print(f"\nRunning pipeline on {len(pipeline_input.urls)} URL(s)...\n")
    start = time.time()

    result, diag = run_pipeline(pipeline_input)

    write_output_excel(result, pipeline_input.columns, output_path, diag=diag)

    elapsed = int(time.time() - start)

    print_run_summary(result, pipeline_input, diag or {}, elapsed)
    print(f"\nResults saved to '{output_path}' - completed in {format_elapsed(elapsed)}")


if __name__ == "__main__":
    main()
