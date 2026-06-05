from pydantic import BaseModel, Field


class FetchedPage(BaseModel):
    """Output of the Acquire layer — one fetched (or cached) page."""
    url: str
    parent_url: str | None = None
    markdown: str
    status: str  # "ok" | "cached" | "error"
    depth: int = 0
    crawl_score: float = 1.0
    fetch_time_ms: int = 0


class LinkCandidate(BaseModel):
    """Link discovered during guided crawling."""
    url: str
    anchor_text: str = ""
    depth: int = 0
    score: float = 0.0
    parent_url: str | None = None


class EntityDoc(BaseModel):
    """Container for all pages associated with one entity."""
    start_url: str
    pages: list = Field(default_factory=list)  # list[PageDoc]
