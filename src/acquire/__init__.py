from models import Config, ColumnSpec, PageDoc
from src.acquire.cache import read_cache, write_cache
from src.acquire.crawler import crawl_entity
from src.acquire.fetcher import _FETCHERS
from src.acquire.models import FetchedPage

__all__ = ["acquire", "FetchedPage"]


def acquire(
    urls: list[tuple[str, int]],
    cfg: Config,
    columns: list[ColumnSpec] | None = None,
) -> list[FetchedPage]:
    """
    Fetch each URL and return one FetchedPage per acquired page.

    urls:    list of (url, depth) — depth is reserved for future per-URL crawl
             depth control; currently unused. cfg.default_depth is the fallback.
    columns: pass the extraction columns to enable the guided-crawl path.
             When None (or CRAWL_ENABLED is False in the caller), the direct
             single-fetch path is used.

    Two internal paths are preserved and not yet merged:
      - Crawl path  (columns provided): crawl_entity() → list[PageDoc]
                                        converted to list[FetchedPage]
      - Direct path (columns is None):  fetcher backends → markdown → FetchedPage
    """
    fetcher = _FETCHERS.get(cfg.acquire_tool)
    if fetcher is None:
        raise ValueError(
            f"Unknown acquire_tool: {cfg.acquire_tool!r}. Choose from: {list(_FETCHERS)}"
        )

    results: list[FetchedPage] = []

    for url, _depth in urls:
        if columns is not None:
            # Crawl path: crawl_entity returns PageDoc objects; bridge to FetchedPage.
            entity_doc = crawl_entity(url, columns)
            for page_doc in entity_doc.pages:
                results.append(FetchedPage(
                    url=page_doc.url,
                    parent_url=None,
                    markdown=page_doc.text,
                    status="cached" if page_doc.from_cache else "ok",
                ))
        else:
            # Direct fetch path.
            cached = read_cache(url, cfg.cache_dir)
            if cached is not None:
                results.append(FetchedPage(url=url, parent_url=None, markdown=cached, status="cached"))
                continue
            try:
                markdown = fetcher(url, cfg)
                write_cache(url, markdown, cfg.cache_dir)
                results.append(FetchedPage(url=url, parent_url=None, markdown=markdown, status="ok"))
            except Exception as e:
                print(f"    [FAIL] acquire {url}: {e}")
                results.append(FetchedPage(url=url, parent_url=None, markdown="", status="error"))

    return results
