from pydantic import BaseModel, Field
from typing import Any


class ColumnSpec(BaseModel):
    name: str
    instruction: str | None = None


class PageDoc(BaseModel):
    url: str
    text: str
    html: str | None = None
    from_cache: bool = False


class ExtractedCell(BaseModel):
    url: str
    column: str
    value: Any = None
    quote: str | None = None
    verified: bool = False
    verification_score: float | None = None


class ExtractedRow(BaseModel):
    url: str
    cells: list[ExtractedCell] = Field(default_factory=list)


class PipelineResult(BaseModel):
    rows: list[ExtractedRow] = Field(default_factory=list)