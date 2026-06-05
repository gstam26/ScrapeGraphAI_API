import time
from collections import defaultdict
from typing import Any

from config import (
    ACQUIRE_TOOL,
    API_KEY,
    CACHE_DIR,
    CRAWL_MAX_PAGES,
    CRAWL_MIN_SCORE,
    DEFAULT_DEPTH,
    EXTRACT_TOOL,
    FETCH_BACKEND,
    REQUEST_HEADERS,
)
from aggregate import aggregate_cells
from extract import extract_cells
from filter import filter_page
from models import ColumnSpec, Config, ExtractedRow, PageDoc, PipelineInput, PipelineResult, UrlSpec
from src.acquire import FetchedPage, acquire
from verify import verify_cells


def _build_config(overrides: dict[str, Any] | None = None) -> Config:
    cfg = Config(
        acquire_tool=ACQUIRE_TOOL,
        extract_tool=EXTRACT_TOOL,
        cache_dir=CACHE_DIR,
        request_headers=REQUEST_HEADERS,
        sgai_api_key=API_KEY,
        default_depth=DEFAULT_DEPTH,
        crawl_min_score=CRAWL_MIN_SCORE,
        crawl_max_pages=CRAWL_MAX_PAGES,
    )

    override_map = {
        "ACQUIRE_TOOL": "acquire_tool",
        "EXTRACT_TOOL": "extract_tool",
        "CRAWL_MIN_SCORE": "crawl_min_score",
        "CRAWL_MAX_PAGES": "crawl_max_pages",
        "DEFAULT_DEPTH": "default_depth",
    }
    for key, value in (overrides or {}).items():
        attr = override_map.get(key.upper())
        if attr:
            setattr(cfg, attr, value)

    return cfg


def _format_elapsed(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    s = int(seconds)
    if s < 60:
        return f"{s}s {ms}ms"
    m, s = divmod(s, 60)
    return f"{m}m {s}s"


def _coerce_pipeline_input(
    pipeline_input: PipelineInput | list[tuple[str, int] | str],
    columns: list[ColumnSpec] | None,
) -> PipelineInput:
    if isinstance(pipeline_input, PipelineInput):
        return pipeline_input

    if columns is None:
        raise ValueError("columns are required when run_pipeline is called with a URL list")

    entities: list[str] = []
    urls: list[UrlSpec] = []
    seen: set[str] = set()

    for entry in pipeline_input:
        if isinstance(entry, str):
            url, depth = entry, 0
        else:
            url, depth = entry
        urls.append(UrlSpec(url=url, depth=depth, entities=[url]))
        if url not in seen:
            entities.append(url)
            seen.add(url)

    return PipelineInput(entities=entities, urls=urls, columns=columns)


def _pages_from_fetch_results(fetch_results: list[FetchedPage]) -> list[PageDoc]:
    return [
        PageDoc(
            url=fp.url,
            text=fp.markdown,
            html=None,
            from_cache=fp.status == "cached",
            depth=fp.depth,
            crawl_score=fp.crawl_score,
            fetch_time_ms=fp.fetch_time_ms,
            backend=fp.backend,
            render_fallback=fp.render_fallback,
            gate_passed=fp.gate_passed,
            gate_reason=fp.gate_reason,
        )
        for fp in fetch_results
    ]


def _annotate_acquire_diag(
    diag: dict,
    acquire_start: int,
    candidate_start: int,
    spec: UrlSpec,
    entities: list[str],
) -> None:
    entity_text = ", ".join(entities)
    for row in diag["acquire_log"][acquire_start:]:
        row["seed_url"] = spec.url
        row["entities"] = entity_text
    for row in diag["crawl_candidates"][candidate_start:]:
        row["seed_url"] = spec.url
        row["entities"] = entity_text


def run_pipeline(
    pipeline_input: PipelineInput | list[tuple[str, int] | str],
    columns: list[ColumnSpec] | None = None,
) -> tuple[PipelineResult, dict]:
    """
    Run the entity extraction pipeline.

    Stages: Acquire -> Filter -> Extract -> Verify -> Aggregate

    Returns (PipelineResult, diag) where diag contains per-layer diagnostic rows.
    """
    request = _coerce_pipeline_input(pipeline_input, columns)
    cfg = _build_config(request.config_overrides)

    diag: dict = {
        "summary": [],
        "acquire_log": [],
        "crawl_candidates": [],
        "extract_log": [],
        "verify_log": [],
    }

    all_entities = list(request.entities)
    cells_by_entity: dict[str, list] = defaultdict(list)
    pages_by_entity: dict[str, set[str]] = defaultdict(set)
    crawled_pages_by_entity: dict[str, set[str]] = defaultdict(set)
    fetch_time_by_entity: dict[str, int] = defaultdict(int)
    extract_time_by_entity: dict[str, int] = defaultdict(int)

    for spec in request.urls:
        relevant_entities = spec.entities or all_entities
        depth = spec.depth

        print(
            f"\n  Processing URL: {spec.url}"
            + (f"  [crawl depth={depth}]" if depth > 0 else "")
            + f"  [entities: {', '.join(relevant_entities)}]"
        )

        try:
            # ========== ACQUIRE ==========
            t_acquire = time.time()
            acquire_start = len(diag["acquire_log"])
            candidate_start = len(diag["crawl_candidates"])

            fetch_results = acquire(
                [(spec.url, depth)],
                cfg,
                columns=request.columns,
                entities=relevant_entities,
                diag=diag,
            )
            _annotate_acquire_diag(diag, acquire_start, candidate_start, spec, relevant_entities)

            pages = _pages_from_fetch_results(fetch_results)

            t_acquire_end = time.time()
            elapsed_acquire = _format_elapsed(t_acquire_end - t_acquire)
            print(f"    OK Acquire: {len(pages)} page(s) - {elapsed_acquire}")

            for entity in relevant_entities:
                for page in pages:
                    pages_by_entity[entity].add(page.url)
                    fetch_time_by_entity[entity] += page.fetch_time_ms
                    if page.depth > 0:
                        crawled_pages_by_entity[entity].add(page.url)

            # ========== FILTER & EXTRACT & VERIFY ==========
            t_extract = time.time()
            url_cells = []

            for page in pages:
                routed = filter_page(page, request.columns)
                cells = extract_cells(
                    routed.page,
                    request.columns,
                    entities=relevant_entities,
                    cfg=cfg,
                    diag=diag,
                )
                cells = verify_cells(cells, routed.page, diag=diag)
                url_cells.extend(cells)
                for cell in cells:
                    cells_by_entity[cell.entity].append(cell)

            t_extract_end = time.time()
            extract_time_ms = int((t_extract_end - t_extract) * 1000)
            elapsed_extract = _format_elapsed(t_extract_end - t_extract)
            for entity in relevant_entities:
                extract_time_by_entity[entity] += extract_time_ms
            print(f"    OK Filter+Extract+Verify: {len(url_cells)} cell(s) - {elapsed_extract}")

        except Exception as e:
            print(f"    X Failed: {e}")
            import traceback
            traceback.print_exc()

    rows = []
    for entity in all_entities:
        all_cells = cells_by_entity.get(entity, [])
        final_cells = aggregate_cells(all_cells)

        all_evidence = [e for c in all_cells for e in c.evidence]
        total_claims = len(all_evidence)
        claims_verified = sum(1 for e in all_evidence if e.verified)
        cells_no_data = sum(1 for c in all_cells if not c.evidence)

        diag["summary"].append({
            "entity": entity,
            "pages_fetched": len(pages_by_entity.get(entity, set())),
            "pages_crawled": len(crawled_pages_by_entity.get(entity, set())),
            "total_claims_found": total_claims,
            "claims_verified": claims_verified,
            "claims_unverified": total_claims - claims_verified,
            "cells_with_no_data": cells_no_data,
            "total_fetch_time": _format_elapsed(fetch_time_by_entity.get(entity, 0) / 1000),
            "total_extract_time": _format_elapsed(extract_time_by_entity.get(entity, 0) / 1000),
            "acquire_tool_used": cfg.acquire_tool,
            "extract_tool_used": cfg.extract_tool,
        })

        rows.append(ExtractedRow(
            entity=entity,
            cells=final_cells,
            all_cells=all_cells,
        ))

    return PipelineResult(rows=rows), diag
