from models import ColumnSpec, ExtractedRow, PipelineResult
from acquire import acquire_page
from filter import filter_page
from extract import extract_cells
from verify import verify_cells


def run_pipeline(urls: list[str], columns: list[ColumnSpec]) -> PipelineResult:
    rows = []

    for url in urls:
        print(f"  Processing: {url}")

        try:
            page = acquire_page(url)
            page = filter_page(page)

            cells = extract_cells(page, columns)
            cells = verify_cells(cells, page)

            rows.append(
                ExtractedRow(
                    url=url,
                    cells=cells,
                )
            )

        except Exception as e:
            print(f"    ✗ Failed: {e}")

            rows.append(
                ExtractedRow(
                    url=url,
                    cells=[],
                )
            )

    return PipelineResult(rows=rows)