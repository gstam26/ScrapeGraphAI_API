import time

from config import ACQUIRE_TOOL, API_KEY, CACHE_DIR, EXTRACT_TOOL, REQUEST_HEADERS
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


def run_pipeline(
    urls: list[tuple[str, int] | str],
    columns: list[ColumnSpec],
) -> tuple[PipelineResult, dict]:
    """
    Run the entity extraction pipeline.

    Stages: Acquire → Filter → Extract → Verify → Aggregate

    Returns (PipelineResult, diag) where diag contains per-layer diagnostic rows.
    """
    cfg = _build_config()
    rows = []

    diag: dict = {
        "summary": [],
        "acquire_log": [],
        "crawl_candidates": [],
        "extract_log": [],
        "verify_log": [],
    }

    for entry in urls:
        if isinstance(entry, str):
            entity_url, depth = entry, cfg.default_depth
        else:
            entity_url, depth = entry

        print(f"\n  Processing entity: {entity_url}" + (f"  [crawl depth={depth}]" if depth > 0 else ""))

        try:
            # ========== ACQUIRE ==========
            t_acquire = time.time()

            fetch_results: list[FetchedPage] = acquire(
                [(entity_url, depth)],
                cfg,
                columns=columns,
                diag=diag,
            )

            pages = [
                PageDoc(
                    url=fp.url,
                    text=fp.markdown,
                    html=None,
                    from_cache=fp.status == "cached",
                    depth=fp.depth,
                    crawl_score=fp.crawl_score,
                    fetch_time_ms=fp.fetch_time_ms,
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
                cells = extract_cells(routed.page, columns, entity_url=entity_url, diag=diag)
                cells = verify_cells(cells, routed.page, entity_url=entity_url, diag=diag)
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

            # ========== SUMMARY ROW ==========
            entity_acquire = [r for r in diag["acquire_log"] if r["entity_url"] == entity_url]
            pages_fetched = len(entity_acquire)
            pages_crawled = sum(1 for r in entity_acquire if r["depth"] > 0)

            all_evidence = [e for c in all_cells for e in c.evidence]
            total_claims = len(all_evidence)
            claims_verified = sum(1 for e in all_evidence if e.verified)
            cells_no_data = sum(1 for c in all_cells if not c.evidence)

            diag["summary"].append({
                "entity_url": entity_url,
                "pages_fetched": pages_fetched,
                "pages_crawled": pages_crawled,
                "total_claims_found": total_claims,
                "claims_verified": claims_verified,
                "claims_unverified": total_claims - claims_verified,
                "cells_with_no_data": cells_no_data,
                "total_fetch_time": elapsed_acquire,
                "total_extract_time": elapsed_filter,
                "acquire_tool_used": cfg.acquire_tool,
                "extract_tool_used": EXTRACT_TOOL,
            })

            rows.append(ExtractedRow(
                entity_url=entity_url,
                cells=final_cells,
                all_cells=all_cells,
            ))

        except Exception as e:
            print(f"    ✗ Failed: {e}")
            import traceback
            traceback.print_exc()
            rows.append(ExtractedRow(entity_url=entity_url, cells=[], all_cells=[]))

    return PipelineResult(rows=rows), diag
