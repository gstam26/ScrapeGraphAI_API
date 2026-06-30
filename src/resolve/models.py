from pydantic import BaseModel, Field


class CompanyInput(BaseModel):
    """One input record. Schema is a fixed contract shared with the upstream
    directory-extractor stage: company, booth, description, categories.

    Only `company` is required; the rest are optional signals used for scoring.
    """
    company: str
    booth: str = ""
    description: str = ""
    categories: str = ""


class Candidate(BaseModel):
    """A single resolution candidate with its score breakdown.

    The component scores are kept on the model (not just the final confidence)
    so the resolver can write an explainable `notes` trail and so the scoring
    can be inspected/tuned without re-running searches.
    """
    url: str
    domain: str = ""            # registrable domain (eTLD+1, heuristic)
    title: str = ""
    snippet: str = ""
    rank: int = 0              # 0-based position in search results

    # Score components, each in 0..1 unless noted.
    name_score: float = 0.0    # company-name ↔ domain similarity (rapidfuzz)
    content_score: float = 0.0  # name+description+categories ↔ page text (keyword/BM25-style)
    rank_score: float = 0.0    # search-rank prior
    embed_boost: float = 0.0   # optional embedding agreement (0 when unreachable)
    penalty: float = 0.0       # blocklist / negative-signal penalty
    confidence: float = 0.0    # final combined score, 0..1

    blocked: bool = False      # domain matched the aggregator/social blocklist
    reasons: list[str] = Field(default_factory=list)


class ResolutionResult(BaseModel):
    """Output record. Schema is a fixed contract — column order in the CSV is:
    company, resolved_url, confidence, candidate_alternatives, needs_review, notes.
    """
    company: str
    resolved_url: str = ""
    confidence: float = 0.0
    candidate_alternatives: list[str] = Field(default_factory=list)
    needs_review: bool = True
    notes: str = ""
