import time

from models import Config, ColumnSpec, PageDoc
from src.acquire.cache import read_cache, write_cache
from src.acquire.fetcher import _FETCHERS
from src.acquire.models import FetchedPage

__all__ = ["acquire", "FetchedPage"]


def acquire(
    urls: list[tuple[str, int] | str],
    cfg: Config,
    columns: list[ColumnSpec] | None = None,
    diag: dict | None = None,
) -> list[FetchedPage]:
    """
    Fetch each URL and return one FetchedPage per acquired page.

    urls: list of (url, depth) tuples, or plain strings (treated as depth=cfg.default_depth).
          Depth 0 = single fetch, no crawl.
          Depth 1+ = guided crawl up to that many hops (requires columns).

    Two internal paths:
      - Direct path (depth=0 or columns is None): fetcher backends → text → FetchedPage
      - Crawl path  (depth≥1 and columns provided): crawl_entity() → list[PageDoc]
                                                     converted to list[FetchedPage]
    """
    fetcher = _FETCHERS.get(cfg.acquire_tool)
    if fetcher is None:
        raise ValueError(
            f"Unknown acquire_tool: {cfg.acquire_tool!r}. Choose from: {list(_FETCHERS)}"
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
            entity_doc = crawl_entity(url, columns, cfg, max_depth=depth, diag=diag)
            for page_doc in entity_doc.pages:
                results.append(FetchedPage(
                    url=page_doc.url,
                    parent_url=None,
                    markdown=page_doc.text,
                    status="cached" if page_doc.from_cache else "ok",
                    depth=page_doc.depth,
                    crawl_score=page_doc.crawl_score,
                    fetch_time_ms=page_doc.fetch_time_ms,
                ))
        else:
            # Direct fetch path: depth=0 or no columns — no crawling.
            cached = read_cache(url, cfg.cache_dir)
            if cached is not None:
                results.append(FetchedPage(url=url, parent_url=None, markdown=cached, status="cached", depth=0))
                if diag is not None:
                    diag.setdefault("acquire_log", []).append({
                        "entity_url": url,
                        "page_url": url,
                        "parent_url": None,
                        "depth": 0,
                        "crawl_score": 1.0,
                        "above_threshold": True,
                        "fetch_tool": cfg.acquire_tool,
                        "page_length": len(cached),
                        "fetch_time_ms": 0,
                        "from_cache": True,
                        "status": "cached",
                        "skip_reason": "",
                    })
                continue
            try:
                t0 = time.time()
                markdown = fetcher(url, cfg)
                fetch_time_ms = int((time.time() - t0) * 1000)
                write_cache(url, markdown, cfg.cache_dir)
                results.append(FetchedPage(
                    url=url, parent_url=None, markdown=markdown,
                    status="ok", depth=0, fetch_time_ms=fetch_time_ms,
                ))
                if diag is not None:
                    diag.setdefault("acquire_log", []).append({
                        "entity_url": url,
                        "page_url": url,
                        "parent_url": None,
                        "depth": 0,
                        "crawl_score": 1.0,
                        "above_threshold": True,
                        "fetch_tool": cfg.acquire_tool,
                        "page_length": len(markdown),
                        "fetch_time_ms": fetch_time_ms,
                        "from_cache": False,
                        "status": "ok",
                        "skip_reason": "",
                    })
            except Exception as e:
                print(f"    [FAIL] acquire {url}: {e}")
                results.append(FetchedPage(url=url, parent_url=None, markdown="", status="error"))
                if diag is not None:
                    diag.setdefault("acquire_log", []).append({
                        "entity_url": url,
                        "page_url": url,
                        "parent_url": None,
                        "depth": 0,
                        "crawl_score": 1.0,
                        "above_threshold": True,
                        "fetch_tool": cfg.acquire_tool,
                        "page_length": 0,
                        "fetch_time_ms": 0,
                        "from_cache": False,
                        "status": "error",
                        "skip_reason": str(e),
                    })

    return results
