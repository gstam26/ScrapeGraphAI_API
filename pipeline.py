import time

from config import CRAWL_ENABLED
from models import ColumnSpec, ExtractedRow, PipelineResult
from acquire import acquire_page
from crawler import crawl_entity
from filter import filter_page
from extract import extract_cells
from verify import verify_cells
from aggregate import aggregate_cells


def _format_elapsed(seconds: float) -> str:
    """Format elapsed time as human-readable string."""
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
    rows = []

    for entity_url in urls:
        print(f"\n  Processing entity: {entity_url}")

        try:
            # ========== ACQUIRE ==========
            t_acquire = time.time()
            
            if CRAWL_ENABLED:
                entity_doc = crawl_entity(entity_url, columns)
                pages = entity_doc.pages
            else:
                pages = [acquire_page(entity_url)]
            
            t_acquire_end = time.time()
            elapsed_acquire = _format_elapsed(t_acquire_end - t_acquire)
            print(f"    ✓ Acquire: {len(pages)} page(s) — {elapsed_acquire}")

            # ========== FILTER & EXTRACT & VERIFY ==========
            t_filter = time.time()
            all_cells = []

            for page in pages:
                # Filter: route page to cells
                routed = filter_page(page, columns)
                
                # Extract: get cells from page
                cells = extract_cells(routed.page, columns)
                
                # Verify: check evidence against page text
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

            rows.append(
                ExtractedRow(
                    entity_url=entity_url,
                    cells=final_cells,
                )
            )

        except Exception as e:
            print(f"    ✗ Failed: {e}")
            import traceback
            traceback.print_exc()

            rows.append(
                ExtractedRow(
                    entity_url=entity_url,
                    cells=[],
                )
            )

    return PipelineResult(rows=rows)
