import time

from config import ACQUIRE_TOOL, API_KEY, CACHE_DIR, CRAWL_ENABLED, REQUEST_HEADERS
from models import ColumnSpec, Config, ExtractedRow, PageDoc, PipelineResult
from src.acquire import FetchedPage, acquire
from filter import filter_page
from extract import extract_cells
from verify import verify_cells
from aggregate import aggregate_cells


def _build_config() -> Config:
    return Config(
        acquire_tool=ACQUIRE_TOOL,
        cache_dir=CACHE_DIR,
        request_headers=REQUEST_HEADERS,
        sgai_api_key=API_KEY,
    )


def _format_elapsed(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    s = int(seconds)

    if s < 60:
        return f"{s}s {ms}ms"

    m, s = divmod(s, 60)
    return f"{m}m {s}s"


def run_pipeline(urls: list[str], columns: list[ColumnSpec]) -> PipelineResult:
    """
    Run the entity extraction pipeline.

    Stages: Acquire → Filter → Extract → Verify → Aggregate
    """
    cfg = _build_config()
    rows = []

    for entity_url in urls:
        print(f"\n  Processing entity: {entity_url}")

        try:
            # ========== ACQUIRE ==========
            t_acquire = time.time()

            fetch_results: list[FetchedPage] = acquire(
                [(entity_url, cfg.default_depth)],
                cfg,
                columns=columns if CRAWL_ENABLED else None,
            )

            # Bridge FetchedPage → PageDoc for the downstream stages (filter /
            # extract / verify still operate on PageDoc; to be unified later).
            pages = [
                PageDoc(
                    url=fp.url,
                    text=fp.markdown,
                    html=None,
                    from_cache=fp.status == "cached",
                )
                for fp in fetch_results
            ]

            t_acquire_end = time.time()
            elapsed_acquire = _format_elapsed(t_acquire_end - t_acquire)
            print(f"    ✓ Acquire: {len(pages)} page(s) — {elapsed_acquire}")

            # ========== FILTER & EXTRACT & VERIFY ==========
            t_filter = time.time()
            all_cells = []

            for page in pages:
                routed = filter_page(page, columns)
                cells = extract_cells(routed.page, columns)
                cells = verify_cells(cells, routed.page)
                all_cells.extend(cells)

            t_filter_end = time.time()
            elapsed_filter = _format_elapsed(t_filter_end - t_filter)
            print(f"    ✓ Filter+Extract+Verify: {len(all_cells)} cell(s) — {elapsed_filter}")

            # ========== AGGREGATE ==========
            t_aggregate = time.time()
            final_cells = aggregate_cells(all_cells)
            t_aggregate_end = time.time()
            elapsed_aggregate = _format_elapsed(t_aggregate_end - t_aggregate)
            print(f"    ✓ Aggregate: {len(final_cells)} cell(s) — {elapsed_aggregate}")

            rows.append(ExtractedRow(entity_url=entity_url, cells=final_cells))

        except Exception as e:
            print(f"    ✗ Failed: {e}")
            import traceback
            traceback.print_exc()
            rows.append(ExtractedRow(entity_url=entity_url, cells=[]))

    return PipelineResult(rows=rows)
