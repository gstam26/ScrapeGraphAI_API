"""Company-name → official-URL resolver.

Standalone subpackage. Takes a CSV of exhibitor/company records
(company, booth, description, categories) and resolves each company to its
single most likely official website, with a confidence score, alternative
candidates, and a needs-review flag.

The input schema (company, booth, description, categories) is a fixed contract:
a later directory-extractor stage is expected to emit the same columns to feed
this resolver. The output schema is likewise fixed — see models.ResolutionResult.

This package reuses the project's already-authorised Firecrawl access and the
acquire-layer fetch path, but imports no pipeline orchestration modules and
modifies none of them.
"""

from src.resolve.models import CompanyInput, Candidate, ResolutionResult
from src.resolve.resolver import resolve_company, resolve_csv

__all__ = [
    "CompanyInput",
    "Candidate",
    "ResolutionResult",
    "resolve_company",
    "resolve_csv",
]
