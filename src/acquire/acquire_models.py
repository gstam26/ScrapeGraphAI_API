"""Pydantic models shared across the Acquire layer.

FetchedPage is the layer's output contract (one record per fetched or cached
page, with fetch provenance); LinkCandidate carries a discovered link plus the
anchor/context text the link scorers rank; EntityDoc groups the pages crawled
for a single entity.
"""

from pydantic import BaseModel, Field


class FetchedPage(BaseModel):
    """Output of the Acquire layer — one fetched (or cached) page."""
    url: str
    parent_url: str | None = None
    markdown: str
    status: str  # "ok" | "cached" | "error" | "gate_failed"
    depth: int = 0
    crawl_score: float = 1.0
    fetch_time_ms: int = 0
    # Fetch provenance — inspectable for evaluation
    backend: str = ""          # "local_static" | "local_render" | "firecrawl" | "sgai" | "cache" | ...
    render_fallback: bool = False  # True when Playwright re-render was used after gate failure
    gate_passed: bool | None = None  # None = gate not run (cached or non-local backend)
    gate_reason: str = ""      # empty when passed or not run; failure reason when gate_passed=False


class LinkCandidate(BaseModel):
    """Link discovered during guided crawling."""
    url: str
    anchor_text: str = ""
    depth: int = 0
    score: float = 0.0
    parent_url: str | None = None
    context: str = ""  # surrounding text extracted from the page (±120 chars)


class EntityDoc(BaseModel):
    """Container for all pages associated with one entity."""
    start_url: str
    pages: list = Field(default_factory=list)  # list[PageDoc]
