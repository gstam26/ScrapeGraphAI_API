import math
import re
from urllib.parse import urlparse

from src.acquire.models import LinkCandidate

BM25_K1 = 1.5
BM25_B = 0.75

_STOP = frozenset({
    "the", "and", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "an", "as", "is", "are", "be", "not",
    "from", "that", "this", "it", "its", "your", "our", "their",
    "you", "we", "they", "was", "were", "has", "have", "had",
    "more", "read", "view", "all", "shop", "buy", "learn",
})

def _tokenize(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) >= 3 and w not in _STOP
    ]


def _bm25_prepare(
    tokenized_docs: list[list[str]],
) -> tuple[list[dict[str, int]], dict[str, float], float]:
    tf_docs: list[dict[str, int]] = []
    doc_freq: dict[str, int] = {}

    for doc in tokenized_docs:
        tf: dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        for term in set(doc):
            doc_freq[term] = doc_freq.get(term, 0) + 1
        tf_docs.append(tf)

    n_docs = len(tokenized_docs)
    avg_doc_len = sum(len(d) for d in tokenized_docs) / max(n_docs, 1)

    idf = {
        term: max(0.0, math.log(1 + ((n_docs - df + 0.5) / (df + 0.5))))
        for term, df in doc_freq.items()
    }
    return tf_docs, idf, avg_doc_len


def _bm25_score_doc(
    query_tokens: list[str],
    doc_tokens: list[str],
    tf: dict[str, int],
    idf: dict[str, float],
    avg_doc_len: float,
    term_weights: dict[str, float] | None = None,
) -> float:
    if not query_tokens or not doc_tokens or avg_doc_len <= 0:
        return 0.0

    doc_len = len(doc_tokens)
    score = 0.0
    for term in set(query_tokens):
        freq = tf.get(term, 0)
        if freq == 0:
            continue
        denom = freq + BM25_K1 * (1 - BM25_B + BM25_B * (doc_len / avg_doc_len))
        bm25 = idf.get(term, 0.0) * ((freq * (BM25_K1 + 1)) / denom)
        score += (term_weights.get(term, 1.0) if term_weights else 1.0) * bm25
    return score


def score_links(candidates: list[LinkCandidate], crawl_query: dict[str, float]) -> list[LinkCandidate]:
    """
    Score all candidates using BM25 over anchor text + surrounding context + URL path.

    crawl_query maps pre-tokenized terms to their query weights.
    Scores are normalised to 0-1 per call (relative ranking within this batch).
    """
    if not candidates:
        return candidates

    query_tokens = list(crawl_query.keys())

    doc_texts = [
        f"{c.anchor_text} {c.context} {urlparse(c.url).path}"
        for c in candidates
    ]
    tokenized_docs = [_tokenize(t) for t in doc_texts]
    tf_docs, idf, avg_doc_len = _bm25_prepare(tokenized_docs)

    raw_scores = [
        _bm25_score_doc(query_tokens, doc_tokens, tf, idf, avg_doc_len, term_weights=crawl_query)
        for doc_tokens, tf in zip(tokenized_docs, tf_docs)
    ]

    max_score = max(raw_scores) if raw_scores else 0.0
    for candidate, raw in zip(candidates, raw_scores):
        candidate.score = raw / max_score if max_score > 0 else 0.0

    return candidates


def score_links_embed(
    candidates: list[LinkCandidate],
    questions: list[str],
) -> list[LinkCandidate]:
    """
    Score candidates using Ollama nomic-embed-text cosine similarity.

    Each question is embedded separately; a candidate's score is the MAX
    cosine across all question vectors. This prevents a diluted joint query
    from suppressing pages that are strongly relevant to just one question.
    Entity names are excluded — they belong in link hygiene, not scoring.

    Returns absolute cosine scores (not per-batch normalised) so the
    CRAWL_MIN_SCORE threshold has consistent meaning across pages.
    Raises on network/embedding failure — caller should fall back to BM25.
    """
    from config import (
        OLLAMA_QUERY_PREFIX, OLLAMA_DOC_PREFIX,
        INFORMATIONAL_REF, TRANSACTIONAL_REF, PAGE_TYPE_ALPHA,
    )
    from src.embed import embed_batch

    if not candidates or not questions:
        return candidates

    query_texts = [OLLAMA_QUERY_PREFIX + q for q in questions]
    ref_texts = [
        OLLAMA_DOC_PREFIX + INFORMATIONAL_REF,
        OLLAMA_DOC_PREFIX + TRANSACTIONAL_REF,
    ]
    doc_texts = [
        OLLAMA_DOC_PREFIX + f"{c.anchor_text} {c.context} {urlparse(c.url).path}".strip()
        for c in candidates
    ]

    all_texts = query_texts + ref_texts + doc_texts
    embs = embed_batch(all_texts)

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    q_vecs = embs[:len(questions)]
    informational_ref_emb = embs[len(questions)]
    transactional_ref_emb = embs[len(questions) + 1]
    d_vecs = embs[len(questions) + 2:]

    for candidate, dv in zip(candidates, d_vecs):
        topic_score = max(_cosine(qv, dv) for qv in q_vecs)
        info_score = _cosine(dv, informational_ref_emb)
        trans_score = _cosine(dv, transactional_ref_emb)
        type_score = info_score - trans_score
        candidate.score = topic_score * (1 + PAGE_TYPE_ALPHA * type_score)

    return sorted(candidates, key=lambda c: c.score, reverse=True)
