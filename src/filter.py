import math
import re

from config import (
    FILTER_CHUNK_SIZE,
    FILTER_THRESHOLD,
    OLLAMA_DOC_PREFIX,
    OLLAMA_QUERY_PREFIX,
)
from models import ColumnSpec, PageDoc, RoutedPage
from src.embed import embed_batch

# Hard cap on chunks per page to bound embedding cost on very long pages.
_MAX_CHUNKS_PER_PAGE = 100

# Stopwords excluded when extracting significant keywords from column names.
_STOP = frozenset({
    "the", "and", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "an", "as", "is", "are", "be", "not",
    "from", "that", "this", "it", "its", "your", "our", "their",
    "you", "we", "they", "was", "were", "has", "have", "had",
    "what", "which", "who", "whom", "how", "when", "where", "why",
    "does", "did", "do", "can", "could", "should", "would", "will",
})

# Question embeddings cached by column-name tuple — computed once per unique
# column set per process, not once per page.
_question_emb_cache: dict[tuple[str, ...], list[list[float]]] = {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _chunk_text(text: str, chunk_size: int = FILTER_CHUNK_SIZE) -> list[str]:
    """Split text into ~chunk_size character chunks on paragraph boundaries.

    Paragraphs are accumulated until adding the next one would exceed
    chunk_size; a single paragraph longer than chunk_size is hard-split.
    Capped at _MAX_CHUNKS_PER_PAGE chunks (the first 100).
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > chunk_size:
            # Flush whatever has accumulated, then hard-split the long paragraph.
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), chunk_size):
                chunks.append(para[i:i + chunk_size])
            continue

        if current and len(current) + 1 + len(para) > chunk_size:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n{para}" if current else para

    if current:
        chunks.append(current)

    return chunks[:_MAX_CHUNKS_PER_PAGE]


def _keywords(column_name: str) -> set[str]:
    """Significant keywords: words longer than 3 chars, excluding stopwords."""
    return {
        w for w in re.findall(r"[a-z0-9]+", column_name.lower())
        if len(w) > 3 and w not in _STOP
    }


def filter_page(page: PageDoc, columns: list[ColumnSpec] | None = None) -> RoutedPage:
    """
    Route a page to extraction with cell relevance markers.

    The page text is split into ~FILTER_CHUNK_SIZE-char chunks (paragraph
    boundaries preferred, capped at 100 chunks) and all chunks are embedded
    in one call. A column's page score is the MAX cosine across chunks, so a
    single relevant paragraph is enough — long pages no longer dilute the
    signal the way a single whole-page embedding did.

    A column is relevant if EITHER gate passes:
      1. max chunk cosine >= FILTER_THRESHOLD
      2. any significant keyword from the column name appears in the page
         text (case-insensitive)

    Question embeddings are cached per unique column set — computed once per
    run, not per page. Falls back to all columns if no column clears either
    gate or if the embedding endpoint is unreachable — never produces an
    empty relevant_columns.
    """
    all_columns = columns or []
    all_names = [col.name for col in all_columns]
    full_set = set(all_names)

    if not all_columns or not page.text:
        return RoutedPage(page=page, relevant_columns=full_set)

    try:
        cache_key = tuple(all_names)
        chunks = _chunk_text(page.text)
        chunk_texts = [OLLAMA_DOC_PREFIX + c for c in chunks]

        if cache_key not in _question_emb_cache:
            # First call: batch questions + this page's chunks in one call.
            question_texts = [OLLAMA_QUERY_PREFIX + name for name in all_names]
            all_embs = embed_batch(question_texts + chunk_texts)
            _question_emb_cache[cache_key] = all_embs[:len(all_names)]
            chunk_embs = all_embs[len(all_names):]
        else:
            chunk_embs = embed_batch(chunk_texts)

        question_embs = _question_emb_cache[cache_key]
        page_text_lower = page.text.lower()

        relevant_columns = set()
        for name, qv in zip(all_names, question_embs):
            max_score = max(
                (_cosine(cv, qv) for cv in chunk_embs), default=0.0
            )
            if max_score >= FILTER_THRESHOLD:
                relevant_columns.add(name)
                continue
            if any(kw in page_text_lower for kw in _keywords(name)):
                relevant_columns.add(name)

        if not relevant_columns:
            relevant_columns = full_set

    except Exception as exc:
        print(f"    [filter] embedding failed ({exc}); routing all columns")
        relevant_columns = full_set

    return RoutedPage(page=page, relevant_columns=relevant_columns)
