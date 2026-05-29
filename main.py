import time
import os
import pandas as pd
from config import API_KEY, FETCH_WAIT_MS
from scraper import scrape_url, parse_columns
from output import write_output_excel


def get_urls_from_excel(filepath: str) -> list[str]:
    df = pd.read_excel(filepath)
    url_col = next(
        (col for col in df.columns if "url" in col.lower() or "link" in col.lower()),
        df.columns[0]
    )
    return df[url_col].dropna().tolist()


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


def main():
    print("=== Web Scraper ===\n")
    input_path = input("Path to input Excel file (e.g. urls.xlsx): ").strip()
    urls = get_urls_from_excel(input_path)
    print(f"\n✓ Loaded {len(urls)} URL(s) from '{input_path}'")

    raw_columns = get_columns_from_user()
    if not raw_columns:
        print("No columns provided. Exiting.")
        return

    # Parse once to get clean headers for output
    clean_headers, _ = parse_columns(raw_columns)

    filename = input("\nOutput Excel filename (e.g. results.xlsx): ").strip()
    if not filename.endswith(".xlsx"):
        filename += ".xlsx"
    os.makedirs("outputs", exist_ok=True)
    output_path = os.path.join("outputs", filename)

    print(f"\nScraping {len(urls)} URL(s)...\n")
    start = time.time()

    results = []
    for url in urls:
        print(f"  Scraping: {url}")
        row, _ = scrape_url(url, raw_columns, API_KEY, FETCH_WAIT_MS)
        results.append(row)

    write_output_excel(results, clean_headers, output_path)
    elapsed = int(time.time() - start)
    d, remainder = divmod(elapsed, 86400)
    h, remainder = divmod(remainder, 3600)
    m, s = divmod(remainder, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    print(f"\n✓ Results saved to '{output_path}' — completed in {' '.join(parts)}")


if __name__ == "__main__":
    main()
