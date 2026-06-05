from pydantic import BaseModel, Field


class FetchedPage(BaseModel):
    """Output of the Acquire layer — one fetched (or cached) page."""
    url: str
    parent_url: str | None = None
    markdown: str
    status: str  # "ok" | "cached" | "error"


class LinkCandidate(BaseModel):
    """Link discovered during guided crawling."""
    url: str
    anchor_text: str = ""
    depth: int = 0
    score: float = 0.0


class EntityDoc(BaseModel):
    """Container for all pages associated with one entity."""
    start_url: str
    pages: list = Field(default_factory=list)  # list[PageDoc]
