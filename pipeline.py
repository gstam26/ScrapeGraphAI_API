import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from config import (
    ACQUIRE_TOOL,
    API_KEY,
    CACHE_DIR,
    CRAWL_MAX_PAGES,
    CRAWL_MIN_SCORE,
    CRAWL_MIN_SCORE_EMBED,
    CRAWL_SCORER,
    DEFAULT_DEPTH,
    EXTRACT_TOOL,
    EXTRACT_PAGE_WORKERS,
    FETCH_BACKEND,
    GROUPING_ENABLED,
    PIPELINE_ENTITY_WORKERS,
    REQUEST_HEADERS,
)
from src.aggregate import aggregate_cells, _is_list_column
from src.group import group_rows
from src.extract import extract_cells
from src.filter import filter_page
from models import ColumnSpec, Config, ExtractedRow, PageDoc, PipelineInput, PipelineResult, UrlSpec
from src.acquire import FetchedPage, acquire
from src.verify import verify_cells


def _build_config(overrides: dict[str, Any] | None = None) -> Config:
    cfg = Config(
        acquire_tool=ACQUIRE_TOOL,
        extract_tool=EXTRACT_TOOL,
        cache_dir=CACHE_DIR,
        request_headers=REQUEST_HEADERS,
        sgai_api_key=API_KEY,
        default_depth=DEFAULT_DEPTH,
        crawl_min_score=CRAWL_MIN_SCORE,
        crawl_min_score_embed=CRAWL_MIN_SCORE_EMBED,
        crawl_max_pages=CRAWL_MAX_PAGES,
        crawl_scorer=CRAWL_SCORER,
    )

    override_map = {
        "ACQUIRE_TOOL": "acquire_tool",
        "EXTRACT_TOOL": "extract_tool",
        "CRAWL_MIN_SCORE": "crawl_min_score",
        "CRAWL_MIN_SCORE_EMBED": "crawl_min_score_embed",
        "CRAWL_MAX_PAGES": "crawl_max_pages",
        "CRAWL_SCORER": "crawl_scorer",
        "DEFAULT_DEPTH": "default_depth",
    }
    for key, value in (overrides or {}).items():
        attr = override_map.get(key.upper())
        if attr:
            setattr(cfg, attr, value)

    return cfg


def _safe_print(msg: str) -> None:
    """Print that never raises on encoding.

    A workbook entity name or URL containing a character outside the
    console's active codepage (common on Windows without PYTHONIOENCODING
    set) would otherwise raise UnicodeEncodeError from a bare print() call —
    2026-07-03 code review found this sitting outside _process_url_spec's
    try block, where it propagated through the unguarded future.result() in
    run_pipeline and discarded every already-completed entity's results.
    """
    try:
        print(msg)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(encoding, errors="replace").decode(encoding))


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


def _process_url_spec(
    spec: UrlSpec,
    request: PipelineInput,
    cfg: Config,
    all_entities: list[str],
) -> dict[str, Any]:
    """
    Run Acquire -> Filter -> Extract -> Verify for one URL spec.

    Thread-safe by construction: everything is accumulated in a local diag and
    returned; run_pipeline merges results in the main thread, in original spec
    order, so diagnostic sheets stay deterministic regardless of completion
    order. One spec = one seed domain, so entity-level parallelism does not
    raise per-domain request rates.
    """
    relevant_entities = spec.entities or all_entities
    depth = spec.depth

    local_diag: dict = {
        "acquire_log": [],
        "crawl_candidates": [],
        "filter_log": [],
        "extract_log": [],
        "verify_log": [],
    }
    result: dict[str, Any] = {
        "entities": relevant_entities,
        "diag": local_diag,
        "pages": [],
        "cells": [],
        "extract_time_ms": 0,
        "error": None,
    }

    _safe_print(
        f"\n  Processing URL: {spec.url}"
        + (f"  [crawl depth={depth}]" if depth > 0 else "")
        + f"  [entities: {', '.join(relevant_entities)}]"
    )

    try:
        # ========== ACQUIRE ==========
        t_acquire = time.time()

        fetch_results = acquire(
            [(spec.url, depth)],
            cfg,
            columns=request.columns,
            entities=relevant_entities,
            diag=local_diag,
        )
        _annotate_acquire_diag(local_diag, 0, 0, spec, relevant_entities)

        pages = _pages_from_fetch_results(fetch_results)
        result["pages"] = pages

        elapsed_acquire = _format_elapsed(time.time() - t_acquire)
        _safe_print(f"    OK Acquire [{spec.url}]: {len(pages)} page(s) - {elapsed_acquire}")

        # ========== FILTER & EXTRACT & VERIFY ==========
        t_extract = time.time()
        url_cells: list = []

        def process_page(index: int, page: PageDoc) -> dict[str, Any]:
            page_diag: dict = {"filter_log": [], "extract_log": [], "verify_log": []}
            routed = filter_page(page, request.columns, diag=page_diag)
            relevant_cols = [c for c in request.columns if c.name in routed.relevant_columns]
            cells = extract_cells(
                routed.page,
                relevant_cols,
                entities=relevant_entities,
                cfg=cfg,
                diag=page_diag,
            )
            cells = verify_cells(cells, routed.page, diag=page_diag)
            return {
                "index": index,
                "cells": cells,
                "diag": page_diag,
            }

        page_results: list[dict[str, Any] | None] = [None for _ in pages]
        max_workers = min(EXTRACT_PAGE_WORKERS, len(pages)) if pages else 1
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(process_page, index, page): index
                for index, page in enumerate(pages)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    page_results[index] = future.result()
                except Exception as exc:
                    page_results[index] = {
                        "index": index,
                        "cells": [],
                        "diag": {"filter_log": [], "extract_log": [], "verify_log": []},
                        "error": exc,
                    }

        for page_result in page_results:
            if not page_result:
                continue
            if page_result.get("error"):
                print(f"    X Page failed: {page_result['error']}")
                continue
            page_diag = page_result["diag"]
            local_diag["filter_log"].extend(page_diag.get("filter_log", []))
            local_diag["extract_log"].extend(page_diag.get("extract_log", []))
            local_diag["verify_log"].extend(page_diag.get("verify_log", []))
            url_cells.extend(page_result["cells"])

        result["cells"] = url_cells
        t_extract_end = time.time()
        result["extract_time_ms"] = int((t_extract_end - t_extract) * 1000)
        elapsed_extract = _format_elapsed(t_extract_end - t_extract)
        _safe_print(f"    OK Filter+Extract+Verify [{spec.url}]: {len(url_cells)} cell(s) - {elapsed_extract}")

    except Exception as e:
        _safe_print(f"    X Failed [{spec.url}]: {e}")
        import traceback
        traceback.print_exc()
        result["error"] = e

    return result


def run_pipeline(
    pipeline_input: PipelineInput | list[tuple[str, int] | str],
    columns: list[ColumnSpec] | None = None,
) -> tuple[PipelineResult, dict]:
    """
    Run the entity extraction pipeline.

    Stages: Acquire -> Filter -> Extract -> Verify -> Aggregate

    URL specs are processed concurrently (PIPELINE_ENTITY_WORKERS) — each spec
    is a different seed domain, so per-domain request rates are unchanged.

    Returns (PipelineResult, diag) where diag contains per-layer diagnostic rows.
    """
    request = _coerce_pipeline_input(pipeline_input, columns)
    cfg = _build_config(request.config_overrides)

    diag: dict = {
        "summary": [],
        "acquire_log": [],
        "crawl_candidates": [],
        "filter_log": [],
        "extract_log": [],
        "verify_log": [],
    }

    all_entities = list(request.entities)
    cells_by_entity: dict[str, list] = defaultdict(list)
    pages_by_entity: dict[str, set[str]] = defaultdict(set)
    crawled_pages_by_entity: dict[str, set[str]] = defaultdict(set)
    fetch_time_by_entity: dict[str, int] = defaultdict(int)
    extract_time_by_entity: dict[str, int] = defaultdict(int)

    spec_results: list[dict[str, Any] | None] = [None for _ in request.urls]
    max_spec_workers = max(1, min(PIPELINE_ENTITY_WORKERS, len(request.urls))) if request.urls else 1
    with ThreadPoolExecutor(max_workers=max_spec_workers) as ex:
        futures = {
            ex.submit(_process_url_spec, spec, request, cfg, all_entities): index
            for index, spec in enumerate(request.urls)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                spec_results[index] = future.result()
            except Exception as exc:
                # _process_url_spec already catches everything it can attribute
                # to a specific spec; this is the backstop so one spec's
                # unexpected failure (e.g. a bug outside that try block) can
                # never discard every other already-completed entity's results
                # (2026-07-03 code review) — mirrors the page-level pattern
                # a few lines below in _process_url_spec.
                print(f"    X Spec crashed unexpectedly [{request.urls[index].url}]: {exc}")
                import traceback
                traceback.print_exc()
                spec_results[index] = {
                    "entities": request.urls[index].entities or all_entities,
                    "diag": {"acquire_log": [], "crawl_candidates": [], "filter_log": [], "extract_log": [], "verify_log": []},
                    "pages": [], "cells": [], "extract_time_ms": 0, "error": exc,
                }

    # Merge in original spec order (single-threaded) so diagnostic sheets are
    # deterministic regardless of which spec finished first.
    for spec_result in spec_results:
        if not spec_result:
            continue
        local_diag = spec_result["diag"]
        for key in ("acquire_log", "crawl_candidates", "filter_log", "extract_log", "verify_log"):
            diag[key].extend(local_diag.get(key, []))

        relevant_entities = spec_result["entities"]
        for entity in relevant_entities:
            for page in spec_result["pages"]:
                pages_by_entity[entity].add(page.url)
                fetch_time_by_entity[entity] += page.fetch_time_ms
                if page.depth > 0:
                    crawled_pages_by_entity[entity].add(page.url)
            extract_time_by_entity[entity] += spec_result["extract_time_ms"]

        for cell in spec_result["cells"]:
            cells_by_entity[cell.entity].append(cell)

    list_columns = {c.name for c in request.columns if _is_list_column(c.instruction)}

    rows = []
    for entity in all_entities:
        all_cells = cells_by_entity.get(entity, [])
        final_cells = aggregate_cells(all_cells, list_columns=list_columns)

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

    # Deterministic claim grouping (Grouped Themes sheet). Strictly additive:
    # reads the aggregated rows, never mutates them. Any failure — including
    # an unreachable Ollama host off-network — only skips the sheet; the run
    # itself must never fail because of grouping.
    if GROUPING_ENABLED:
        try:
            claim_groups = group_rows(rows)
            if claim_groups:
                diag["claim_groups"] = claim_groups
        except Exception as exc:
            _safe_print(f"! Grouping skipped: {exc}")

    return PipelineResult(rows=rows), diag
