import time

from models import Config, ColumnSpec, PageDoc
from src.acquire.cache import read_cache, write_cache
from src.acquire.fetcher import fetch_page_with_provenance, _VALID_BACKENDS
from src.acquire.models import FetchedPage

__all__ = ["acquire", "FetchedPage"]


def acquire(
    urls: list[tuple[str, int] | str],
    cfg: Config,
    columns: list[ColumnSpec] | None = None,
    entities: list[str] | None = None,
    diag: dict | None = None,
) -> list[FetchedPage]:
    """
    Fetch each URL and return one FetchedPage per acquired page.

    urls: list of (url, depth) tuples, or plain strings (treated as depth=cfg.default_depth).
          Depth 0 = single fetch, no crawl.
          Depth 1+ = guided crawl up to that many hops (requires columns).

    Two internal paths:
      - Direct path (depth=0 or columns is None): fetch_page_with_provenance → FetchedPage
      - Crawl path  (depth≥1 and columns provided): crawl_entity() → list[PageDoc]
                                                     converted to list[FetchedPage]
    """
    if cfg.acquire_tool not in _VALID_BACKENDS:
        raise ValueError(
            f"Unknown acquire_tool: {cfg.acquire_tool!r}. "
            f"Choose from: {sorted(_VALID_BACKENDS)}"
        )

    results: list[FetchedPage] = []

    for entry in urls:
        if isinstance(entry, str):
            url, depth = entry, cfg.default_depth
        else:
            url, depth = entry

        if depth > 0 and columns is not None:
            # Crawl path: crawl_entity returns PageDoc objects; bridge to FetchedPage.
            from src.acquire.crawler import crawl_entity  # lazy: avoids config.py at import time
            entity_doc = crawl_entity(url, columns, cfg, max_depth=depth, entities=entities, diag=diag)
            for page_doc in entity_doc.pages:
                results.append(FetchedPage(
                    url=page_doc.url,
                    parent_url=None,
                    markdown=page_doc.text,
                    status="cached" if page_doc.from_cache else (
                        "gate_failed" if page_doc.gate_passed is False else "ok"
                    ),
                    depth=page_doc.depth,
                    crawl_score=page_doc.crawl_score,
                    fetch_time_ms=page_doc.fetch_time_ms,
                    backend=page_doc.backend,
                    render_fallback=page_doc.render_fallback,
                    gate_passed=page_doc.gate_passed,
                    gate_reason=page_doc.gate_reason,
                ))
        else:
            # Direct fetch path: depth=0 or no columns — no crawling.
            cached = read_cache(url, cfg.cache_dir)
            if cached is not None:
                results.append(FetchedPage(
                    url=url, parent_url=None, markdown=cached, status="cached", depth=0,
                    backend="cache", render_fallback=False, gate_passed=None, gate_reason="",
                ))
                if diag is not None:
                    diag.setdefault("acquire_log", []).append({
                        "entity_url": url, "page_url": url, "parent_url": None,
                        "depth": 0, "crawl_score": 1.0, "above_threshold": True,
                        "fetch_tool": cfg.acquire_tool,
                        "page_length": len(cached), "fetch_time_ms": 0,
                        "from_cache": True, "status": "cached", "skip_reason": "",
                        "backend": "cache", "render_fallback": False,
                        "gate_passed": None, "gate_reason": "",
                    })
                continue
            try:
                t0 = time.time()
                text, _html, prov = fetch_page_with_provenance(url, cfg)
                fetch_time_ms = int((time.time() - t0) * 1000)
                write_cache(url, text, cfg.cache_dir)
                status = "gate_failed" if prov["gate_passed"] is False else "ok"
                results.append(FetchedPage(
                    url=url, parent_url=None, markdown=text,
                    status=status, depth=0, fetch_time_ms=fetch_time_ms,
                    backend=prov["backend"], render_fallback=prov["render_fallback"],
                    gate_passed=prov["gate_passed"], gate_reason=prov["gate_reason"],
                ))
                if diag is not None:
                    diag.setdefault("acquire_log", []).append({
                        "entity_url": url, "page_url": url, "parent_url": None,
                        "depth": 0, "crawl_score": 1.0, "above_threshold": True,
                        "fetch_tool": cfg.acquire_tool,
                        "page_length": len(text), "fetch_time_ms": fetch_time_ms,
                        "from_cache": False, "status": status, "skip_reason": "",
                        "backend": prov["backend"], "render_fallback": prov["render_fallback"],
                        "gate_passed": prov["gate_passed"], "gate_reason": prov["gate_reason"],
                    })
            except Exception as e:
                print(f"    [FAIL] acquire {url}: {e}")
                results.append(FetchedPage(
                    url=url, parent_url=None, markdown="", status="error",
                    backend=cfg.acquire_tool, render_fallback=False,
                    gate_passed=None, gate_reason="",
                ))
                if diag is not None:
                    diag.setdefault("acquire_log", []).append({
                        "entity_url": url, "page_url": url, "parent_url": None,
                        "depth": 0, "crawl_score": 1.0, "above_threshold": True,
                        "fetch_tool": cfg.acquire_tool,
                        "page_length": 0, "fetch_time_ms": 0,
                        "from_cache": False, "status": "error", "skip_reason": str(e),
                        "backend": cfg.acquire_tool, "render_fallback": False,
                        "gate_passed": None, "gate_reason": "",
                    })

    return results
