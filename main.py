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


def main():
    parser = argparse.ArgumentParser(description="Entity extraction pipeline")
    parser.add_argument("--backend", default="", help="Override ACQUIRE_TOOL (playwright_pooled_hybrid/playwright_pooled/firecrawl/local/playwright/requests)")
    args = parser.parse_args()

    print("=== Entity Extraction Pipeline ===\n")

    input_path = input("Path to input Excel file: ").strip()
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

    filename = input("\nOutput Excel filename: ").strip()

    if not filename.endswith(".xlsx"):
        filename += ".xlsx"

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)

    print(f"\nRunning pipeline on {len(pipeline_input.urls)} URL(s)...\n")
    start = time.time()

    result, diag = run_pipeline(pipeline_input)

    write_output_excel(result, pipeline_input.columns, output_path, diag=diag)

    elapsed = int(time.time() - start)

    print(f"\nResults saved to '{output_path}' - completed in {format_elapsed(elapsed)}")


if __name__ == "__main__":
    main()
