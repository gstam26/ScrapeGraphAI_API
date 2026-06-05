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
        score += idf.get(term, 0.0) * ((freq * (BM25_K1 + 1)) / denom)
    return score


def score_links(candidates: list[LinkCandidate], crawl_terms: list[str]) -> list[LinkCandidate]:
    """
    Score all candidates using BM25 over anchor text + URL path.

    Scores are normalised to 0-1 per call (relative ranking within this batch).
    """
    if not candidates:
        return candidates

    query_tokens = _tokenize(" ".join(crawl_terms))

    doc_texts = [
        f"{c.anchor_text} {urlparse(c.url).path}"
        for c in candidates
    ]
    tokenized_docs = [_tokenize(t) for t in doc_texts]
    tf_docs, idf, avg_doc_len = _bm25_prepare(tokenized_docs)

    raw_scores = [
        _bm25_score_doc(query_tokens, doc_tokens, tf, idf, avg_doc_len)
        for doc_tokens, tf in zip(tokenized_docs, tf_docs)
    ]

    max_score = max(raw_scores) if raw_scores else 0.0
    for candidate, raw in zip(candidates, raw_scores):
        candidate.score = raw / max_score if max_score > 0 else 0.0

    return candidates
