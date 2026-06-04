import re
from config import CRAWL_FALLBACK_TERMS
from models import ColumnSpec


STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "only",
    "return", "give", "show", "find", "extract", "information", "data",
    "value", "values", "list", "item", "items", "field", "fields",
    "about", "page", "website", "webpage"
}


def build_crawl_terms(columns: list[ColumnSpec]) -> list[str]:
    """
    Build crawl intent from user-defined extraction columns.
    This avoids hardcoded domain/brand assumptions.
    """

    schema_text = " ".join(
        f"{col.name} {col.instruction or ''}"
        for col in columns
    ).lower()

    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", schema_text)

    terms = set(CRAWL_FALLBACK_TERMS)

    for token in tokens:
        token = token.lower().replace("-", " ")

        for part in token.split():
            if len(part) > 3 and part not in STOPWORDS:
                terms.add(part)

    return sorted(terms)