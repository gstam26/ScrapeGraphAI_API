import math
import re

from config import (
    FILTER_CHUNK_SIZE,
    FILTER_MODE,
    FILTER_THRESHOLD,
    OLLAMA_DOC_PREFIX,
    OLLAMA_QUERY_PREFIX,
    QUERY_INCLUDES_INSTRUCTION,
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

# Question embeddings cached by the tuple of ACTUAL query texts (not column
# names) — computed once per unique query set per process, not once per page.
# Keying on query texts rather than names means flipping
# QUERY_INCLUDES_INSTRUCTION (or editing an instruction) can never silently
# reuse stale name-only embeddings.
_question_emb_cache: dict[tuple[str, ...], list[list[float]]] = {}


def query_text(col) -> str:
    """Semantic-routing query text for a column.

    With QUERY_INCLUDES_INSTRUCTION (config) enabled, returns
    "<name>. <instruction>" when the column carries a non-empty instruction —
    the instruction is a 30-50 word discriminative probe, far richer than the
    2-3 word name. Falls back to the bare name otherwise (and when the flag
    is False, restoring the old name-only behaviour for A-B comparison).

    Shared by the Filter (score_page_columns) and the crawler's baseline
    embed link scorer so both route on the same query.
    """
    instruction = (getattr(col, "instruction", None) or "").strip()
    if QUERY_INCLUDES_INSTRUCTION and instruction:
        return f"{col.name}. {instruction}"
    return col.name


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


# A keyword shared by at least this fraction of the question set carries no
# routing signal for THAT question set and is dropped from the gate.
_KEYWORD_MAX_QUESTION_FRACTION = 0.5


def _discriminative_keywords(columns: list[ColumnSpec]) -> dict[str, set[str]]:
    """Per-column gate keywords with set-generic terms removed.

    The gate exists to catch pages the embedding under-scores, keyed on words
    specific to ONE question. Words shared across much of the question set
    ("company" in 12/17 CMO questions, "manufacturing" in 5) fire on virtually
    every page of any corporate site, turning the gate into a pass-everything
    OR (2026-07-23 CMO counterfactual: gate fired on 60% of 2,023 page-column
    pairs). Dropping terms present in >= half the questions keeps only the
    discriminative ones (tooling, revenue, headquarters, moulding...).
    Small sets (< 4 questions) are left untouched — a shared word can still
    discriminate when there are few questions to share it.
    """
    per_col = {col.name: _keywords(col.name) for col in columns}
    if len(per_col) >= 4:
        counts: dict[str, int] = {}
        for kws in per_col.values():
            for kw in kws:
                counts[kw] = counts.get(kw, 0) + 1
        cutoff = _KEYWORD_MAX_QUESTION_FRACTION * len(per_col)
        generic = {kw for kw, c in counts.items() if c >= cutoff}
        per_col = {name: kws - generic for name, kws in per_col.items()}
    return per_col


def score_page_columns(text: str, columns: list[ColumnSpec]) -> dict[str, float]:
    """
    Return max-chunk cosine similarity per column name.

    Chunks *text* with _chunk_text(), embeds all chunks in one call, then
    takes the per-column max cosine across chunks. Query texts come from
    query_text() (name + instruction when QUERY_INCLUDES_INSTRUCTION is set).
    Question embeddings are cached per unique query-text set (computed once
    per process, not per page).

    Raises on embedding failure — callers should wrap in try/except.
    Returns {} when text or columns is empty.
    """
    if not text or not columns:
        return {}

    all_names = [col.name for col in columns]
    query_texts = [query_text(col) for col in columns]
    # Cache key MUST be the actual query texts: keying on names alone would
    # silently reuse stale name-only embeddings after the instruction-aware
    # query change (or across a QUERY_INCLUDES_INSTRUCTION flip).
    cache_key = tuple(query_texts)
    chunks = _chunk_text(text)
    chunk_texts = [OLLAMA_DOC_PREFIX + c for c in chunks]

    if cache_key not in _question_emb_cache:
        question_texts = [OLLAMA_QUERY_PREFIX + q for q in query_texts]
        all_embs = embed_batch(question_texts + chunk_texts)
        _question_emb_cache[cache_key] = all_embs[:len(query_texts)]
        chunk_embs = all_embs[len(query_texts):]
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
        # The keyword gate stays on the column NAME only (never the
        # instruction): instruction words like "check", "pages", "company"
        # are generic and would over-fire the gate on almost every page.
        # Names are additionally cross-question filtered — see
        # _discriminative_keywords.
        gate_keywords = _discriminative_keywords(all_columns)
        col_info: dict[str, tuple[float, bool]] = {}
        for name, max_score in scores.items():
            kw_gate = any(kw in page_text_lower for kw in gate_keywords.get(name, set()))
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
