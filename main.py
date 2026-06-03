import os
import time

from config import OUTPUT_DIR
from io_excel import read_urls_from_excel, parse_columns, write_output_excel
from pipeline import run_pipeline


def get_columns_from_user() -> list[str]:
    print("\nEnter column headers. Add a colon after the name to give extraction instructions.")
    print('  Example: "Type of milk: return only the base word, e.g. pea, oat"')
    print('  Example: "Sustainability claims: return as a list, one claim per item"')
    print("Type 'done' when finished.\n")

    columns = []

    while True:
        entry = input(f"  Column {len(columns) + 1}: ").strip()

        if entry.lower() == "done":
            break

        if entry:
            columns.append(entry)

    return columns


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
    print("=== Sustainability Matrix Pipeline ===\n")

    input_path = input("Path to input Excel file: ").strip()
    urls = read_urls_from_excel(input_path)

    print(f"\n✓ Loaded {len(urls)} URL(s) from '{input_path}'")

    raw_columns = get_columns_from_user()

    if not raw_columns:
        print("No columns provided. Exiting.")
        return

    columns = parse_columns(raw_columns)

    filename = input("\nOutput Excel filename: ").strip()

    if not filename.endswith(".xlsx"):
        filename += ".xlsx"

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, filename)

    print(f"\nRunning pipeline on {len(urls)} URL(s)...\n")
    start = time.time()

    result = run_pipeline(urls, columns)

    write_output_excel(result, columns, output_path)

    elapsed = int(time.time() - start)

    print(f"\n✓ Results saved to '{output_path}' — completed in {format_elapsed(elapsed)}")


if __name__ == "__main__":
    main()