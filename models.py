from typing import Any

from pydantic import BaseModel, Field


class ColumnSpec(BaseModel):
    """User-defined extraction question."""
    name: str
    instruction: str | None = None


class UrlSpec(BaseModel):
    """Input URL plus crawl depth and the entities it applies to."""
    url: str
    depth: int = 0
    entities: list[str] = Field(default_factory=list)


class PipelineInput(BaseModel):
    """User-provided extraction request."""
    entities: list[str] = Field(default_factory=list)
    urls: list[UrlSpec] = Field(default_factory=list)
    columns: list[ColumnSpec] = Field(default_factory=list)
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class PageDoc(BaseModel):
    """Raw fetched/cached page content."""
    url: str
    text: str
    html: str | None = None
    from_cache: bool = False
    depth: int = 0
    crawl_score: float = 1.0
    fetch_time_ms: int = 0
    # Fetch provenance — set by acquire layer, None/empty for cached pages
    backend: str = ""          # "local_static" | "local_render" | "firecrawl" | "sgai" | "cache" | ...
    render_fallback: bool = False  # True when Playwright re-render was used after gate failure
    gate_passed: bool | None = None  # None = gate not run (cached or non-local backend)
    gate_reason: str = ""      # empty when passed or not run; failure reason when gate_passed=False


class Config(BaseModel):
    """Runtime configuration passed to each pipeline layer."""
    acquire_tool: str = "local"  # "local" | "requests" | "sgai" | "firecrawl" | "playwright"
    extract_tool: str = "sgai"
    cache_dir: str = "cache"
    request_headers: dict = Field(
        default_factory=lambda: {"User-Agent": "Mozilla/5.0 entity-extraction-pipeline"}
    )
    request_timeout: int = 30
    sgai_api_key: str | None = None
    firecrawl_api_key: str | None = None
    fetch_wait_ms: int = 3000
    default_depth: int = 0
    crawl_min_score: float = 0.12        # BM25 relative threshold
    crawl_min_score_embed: float = 0.50  # Ollama absolute cosine threshold
    crawl_max_pages: int = 2


class SourceQuote(BaseModel):
    """Evidence item: one piece of extracted data with a supporting quote."""
    value: Any = None
    quote: str | None = None
    verified: bool = False
    verification_score: float | None = None
    char_span: tuple[int, int] | None = None
    match_type: str = "none"
    semantic_score: float | None = None


class EvidenceItem(SourceQuote):
    """Alias for SourceQuote."""
    pass


class RoutedPage(BaseModel):
    """Page routed to extraction with cell relevance markers."""
    page: PageDoc
    relevant_columns: set[str] = Field(default_factory=set)


class ExtractedCell(BaseModel):
    """Extracted data for one entity/question/page combination."""
    entity: str = ""
    source_url: str
    column: str
    value: Any = None
    # For list answers: store one evidence item per list element.
    evidence: list[SourceQuote] = Field(default_factory=list)
    # For scalar answers: use primary evidence.
    verified: bool = False
    verification_score: float | None = None


class CellContribution(ExtractedCell):
    """Alias for ExtractedCell."""
    pass


class ExtractedRow(BaseModel):
    """All extracted cells for one entity across multiple pages."""
    entity: str
    cells: list[ExtractedCell] = Field(default_factory=list)        # aggregated (one per question)
    all_cells: list[ExtractedCell] = Field(default_factory=list)    # pre-aggregation (one per page/question)


class PipelineResult(BaseModel):
    """Final output: one row per entity."""
    rows: list[ExtractedRow] = Field(default_factory=list)
