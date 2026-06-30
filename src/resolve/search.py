"""Search layer: company name (+ context) → candidate result URLs.

Search path: Firecrawl's `search` endpoint, reusing the project's already
authorised Firecrawl access. Confirmed present in the installed firecrawl-py
(4.30.3): Firecrawl(...).search(query, limit=...) returns SearchData with a
`.web` list of {url, title, description}. This call runs server-side at
Firecrawl, so it is unaffected by Sagentia's outbound network policy.

There is intentionally NO direct-internet fallback (no requests to DuckDuckGo,
Bing, or Google from this machine). A prior experimental version routed to
Bing and surfaced unsafe results for ambiguous company names — that approach
is rejected. If Firecrawl search fails or returns empty, the company is
reported as unresolved with needs_review=True; nothing is silently guessed.
"""

import os

from dotenv import load_dotenv

from src.resolve.models import Candidate

load_dotenv()

# Records which backend served the most recent search() call, for notes.
last_search_backend: str = ""

DEFAULT_LIMIT = 8


def _build_query(company: str, description: str = "", categories: str = "") -> str:
    """A focused query biased toward the company's own homepage."""
    parts = [company.strip(), "official website"]
    # One category term can disambiguate same-named companies without
    # over-constraining the query.
    cat = (categories or "").split(",")[0].strip()
    if cat:
        parts.append(cat)
    return " ".join(p for p in parts if p)


def _firecrawl_api_key() -> str | None:
    # Mirrors the acquire layer: the SDK reads FIRECRAWL_API_KEY from env when
    # no key is passed explicitly.
    return os.getenv("FIRECRAWL_API_KEY")


def _search_firecrawl(query: str, limit: int) -> list[Candidate]:
    """Only search path. Raises if the SDK has no usable search method or the
    call errors — caller treats that as zero candidates, not a crash."""
    from firecrawl import FirecrawlApp  # same import the acquire layer uses

    app = FirecrawlApp(api_key=_firecrawl_api_key())
    if not hasattr(app, "search"):
        raise AttributeError("FirecrawlApp has no .search() method")

    data = app.search(query, limit=limit, sources=["web"])
    web = _extract_web_results(data)

    candidates: list[Candidate] = []
    for rank, item in enumerate(web):
        url = _result_field(item, "url")
        if not url:
            continue
        candidates.append(
            Candidate(
                url=url,
                title=_result_field(item, "title"),
                snippet=_result_field(item, "description"),
                rank=rank,
            )
        )
    return candidates


def _extract_web_results(data) -> list:
    """SearchData may be a pydantic model (.web) or a dict ({'web': [...]})."""
    if data is None:
        return []
    web = getattr(data, "web", None)
    if web is None and isinstance(data, dict):
        web = data.get("web")
    return list(web or [])


def _result_field(item, field: str) -> str:
    """Read a field from a result that may be a model or a dict."""
    val = getattr(item, field, None)
    if val is None and isinstance(item, dict):
        val = item.get(field)
    return (val or "").strip() if isinstance(val, str) else (val or "")


def search_company(
    company: str,
    description: str = "",
    categories: str = "",
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[Candidate]:
    """Return ranked candidate results for a company via Firecrawl search only.

    On any failure or empty result, returns an empty list and records the
    reason in `last_search_backend` — the resolver then reports the company
    as unresolved with needs_review=True rather than guessing via an
    unauthorised direct-internet fallback.
    """
    global last_search_backend
    query = _build_query(company, description, categories)

    try:
        results = _search_firecrawl(query, limit)
        if results:
            last_search_backend = "firecrawl_search"
            return results
        last_search_backend = "firecrawl_search returned empty"
        return []
    except Exception as e:
        last_search_backend = f"firecrawl_search failed: {type(e).__name__}: {e}"
        return []
