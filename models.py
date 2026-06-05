from typing import Any
from pydantic import BaseModel, Field


class PipelineInput(BaseModel):
    """User-provided extraction request."""
    entity_url: str
    columns: list['ColumnSpec'] = Field(default_factory=list)


class ColumnSpec(BaseModel):
    """User-defined extraction column."""
    name: str
    instruction: str | None = None


class PageDoc(BaseModel):
    """Raw fetched/cached page content."""
    url: str
    text: str
    html: str | None = None
    from_cache: bool = False


class Config(BaseModel):
    """Runtime configuration passed to each pipeline layer."""
    acquire_tool: str = "requests"   # "requests" | "sgai" | "firecrawl" | "playwright"
    cache_dir: str = "cache"
    request_headers: dict = Field(
        default_factory=lambda: {"User-Agent": "Mozilla/5.0 entity-extraction-pipeline"}
    )
    request_timeout: int = 30
    sgai_api_key: str | None = None
    firecrawl_api_key: str | None = None
    fetch_wait_ms: int = 3000
    default_depth: int = 0


class SourceQuote(BaseModel):
    """Evidence item: one piece of extracted data with a supporting quote."""
    value: Any = None
    quote: str | None = None
    verified: bool = False
    verification_score: float | None = None


class EvidenceItem(SourceQuote):
    """Alias for SourceQuote."""
    pass


class RoutedPage(BaseModel):
    """Page routed to extraction with cell relevance markers."""
    page: PageDoc
    relevant_columns: set[str] = Field(default_factory=set)


class ExtractedCell(BaseModel):
    """Extracted data for one entity/column/page combination."""
    source_url: str
    column: str
    value: Any = None
    # For list answers: store one evidence item per list element
    evidence: list[SourceQuote] = Field(default_factory=list)
    # For scalar answers: use primary evidence
    verified: bool = False
    verification_score: float | None = None


class CellContribution(ExtractedCell):
    """Alias for ExtractedCell."""
    pass


class ExtractedRow(BaseModel):
    """All extracted cells for one entity across multiple pages."""
    entity_url: str
    cells: list[ExtractedCell] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """Final output: one row per entity."""
    rows: list[ExtractedRow] = Field(default_factory=list)