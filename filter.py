import math

from config import FILTER_THRESHOLD, OLLAMA_DOC_PREFIX, OLLAMA_QUERY_PREFIX
from models import ColumnSpec, PageDoc, RoutedPage
from src.embed import embed_batch

# Question embeddings cached by column-name tuple — computed once per unique
# column set per process, not once per page.
_question_emb_cache: dict[tuple[str, ...], list[list[float]]] = {}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def filter_page(page: PageDoc, columns: list[ColumnSpec] | None = None) -> RoutedPage:
    """
    Route a page to extraction with cell relevance markers.

    Embeds page.text[:2000] and each question; marks a column relevant if its
    cosine similarity to the page >= FILTER_THRESHOLD. Question embeddings are
    cached per unique column set — computed once per run, not per page.

    Falls back to all columns if no column clears the threshold or if the
    embedding endpoint is unreachable — never produces an empty relevant_columns.
    """
    all_columns = columns or []
    all_names = [col.name for col in all_columns]
    full_set = set(all_names)

    if not all_columns or not page.text:
        return RoutedPage(page=page, relevant_columns=full_set)

    try:
        cache_key = tuple(all_names)
        if cache_key not in _question_emb_cache:
            # First call: batch questions + this page text in one embed_batch call.
            question_texts = [OLLAMA_QUERY_PREFIX + name for name in all_names]
            all_embs = embed_batch(question_texts + [OLLAMA_DOC_PREFIX + page.text[:2000]])
            _question_emb_cache[cache_key] = all_embs[:len(all_names)]
            page_emb = all_embs[len(all_names)]
        else:
            page_emb = embed_batch([OLLAMA_DOC_PREFIX + page.text[:2000]])[0]

        question_embs = _question_emb_cache[cache_key]

        relevant_columns = {
            name
            for name, qv in zip(all_names, question_embs)
            if _cosine(page_emb, qv) >= FILTER_THRESHOLD
        }

        if not relevant_columns:
            relevant_columns = full_set

    except Exception as exc:
        print(f"    [filter] embedding failed ({exc}); routing all columns")
        relevant_columns = full_set

    return RoutedPage(page=page, relevant_columns=relevant_columns)
