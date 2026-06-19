import math
import re

from config import (
    FILTER_CHUNK_SIZE,
    FILTER_MODE,
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


def score_page_columns(text: str, columns: list[ColumnSpec]) -> dict[str, float]:
    """
    Return max-chunk cosine similarity per column name.

    Chunks *text* with _chunk_text(), embeds all chunks in one call, then
    takes the per-column max cosine across chunks. Question embeddings are
    cached per unique column set (computed once per process, not per page).

    Raises on embedding failure — callers should wrap in try/except.
    Returns {} when text or columns is empty.
    """
    if not text or not columns:
        return {}

    all_names = [col.name for col in columns]
    cache_key = tuple(all_names)
    chunks = _chunk_text(text)
    chunk_texts = [OLLAMA_DOC_PREFIX + c for c in chunks]

    if cache_key not in _question_emb_cache:
        question_texts = [OLLAMA_QUERY_PREFIX + name for name in all_names]
        all_embs = embed_batch(question_texts + chunk_texts)
        _question_emb_cache[cache_key] = all_embs[:len(all_names)]
        chunk_embs = all_embs[len(all_names):]
    else:
        chunk_embs = embed_batch(chunk_texts)

    question_embs = _question_emb_cache[cache_key]
    return {
        name: max((_cosine(cv, qv) for cv in chunk_embs), default=0.0)
        for name, qv in zip(all_names, question_embs)
    }


def filter_page(
    page: PageDoc,
    columns: list[ColumnSpec] | None = None,
    diag: dict | None = None,
) -> RoutedPage:
    """
    Route a page to extraction with cell relevance markers.

    Delegates scoring to score_page_columns(). A column is relevant if EITHER
    gate passes:
      1. max chunk cosine >= FILTER_THRESHOLD
      2. any significant keyword from the column name appears in the page text

    Falls back to all columns when none clear either gate or when the
    embedding endpoint is unreachable — never produces an empty relevant_columns.
    """
    all_columns = columns or []
    all_names = [col.name for col in all_columns]
    full_set = set(all_names)

    if not all_columns or not page.text:
        return RoutedPage(page=page, relevant_columns=full_set)

    try:
        scores = score_page_columns(page.text, all_columns)
        page_text_lower = page.text.lower()

        # Step 1: compute scores and keyword gate for every column.
        col_info: dict[str, tuple[float, bool]] = {}
        for name, max_score in scores.items():
            kw_gate = any(kw in page_text_lower for kw in _keywords(name))
            col_info[name] = (max_score, kw_gate)

        # Step 2: decide which columns are relevant based on mode.
        if FILTER_MODE == "passthrough":
            relevant_columns = full_set
            fallback = False
        else:
            relevant_columns = set()
            for name, (max_score, kw_gate) in col_info.items():
                if max_score >= FILTER_THRESHOLD or kw_gate:
                    relevant_columns.add(name)
            fallback = not relevant_columns
            if fallback:
                relevant_columns = full_set

        if diag is not None:
            for name, (emb_score, kw_gate) in col_info.items():
                if FILTER_MODE == "passthrough":
                    reason = "passthrough"
                elif fallback:
                    reason = "fallback_all"
                elif emb_score >= FILTER_THRESHOLD:
                    reason = "embedding_threshold"
                elif kw_gate:
                    reason = "keyword_gate"
                else:
                    reason = "below_threshold"
                diag.setdefault("filter_log", []).append({
                    "url": page.url,
                    "column": name,
                    "embedding_score": round(emb_score, 4),
                    "keyword_gate": kw_gate,
                    "included": name in relevant_columns,
                    "reason": reason,
                })

    except Exception as exc:
        print(f"    [filter] embedding failed ({exc}); routing all columns")
        relevant_columns = full_set
        if diag is not None:
            for col in all_columns:
                diag.setdefault("filter_log", []).append({
                    "url": page.url,
                    "column": col.name,
                    "embedding_score": None,
                    "keyword_gate": False,
                    "included": True,
                    "reason": "fallback_all",
                })

    return RoutedPage(page=page, relevant_columns=relevant_columns)
