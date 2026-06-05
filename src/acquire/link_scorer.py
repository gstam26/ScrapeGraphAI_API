import re
from urllib.parse import urlparse

from src.acquire.models import LinkCandidate


def _tokens(text: str) -> set[str]:
    return set(
        token.lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text)
        if len(token) > 2
    )


def score_link(candidate: LinkCandidate, crawl_terms: list[str]) -> LinkCandidate:
    """
    Cheap relevance scorer.

    Scores links using:
    - URL path
    - anchor text
    - user-derived crawl terms
    - shallow depth preference
    """
    parsed = urlparse(candidate.url)

    link_text = f"{parsed.path} {candidate.anchor_text}".lower()
    link_tokens = _tokens(link_text)
    term_tokens = _tokens(" ".join(crawl_terms))

    if not link_tokens or not term_tokens:
        candidate.score = 0.0
        return candidate

    overlap = link_tokens.intersection(term_tokens)
    score = len(overlap) / max(len(term_tokens), 1)

    if candidate.depth == 0:
        score += 0.20
    elif candidate.depth == 1:
        score += 0.10
    else:
        score -= 0.05

    noisy_markers = ["privacy", "terms", "cookie", "login", "account", "cart", "checkout"]
    if any(marker in candidate.url.lower() for marker in noisy_markers):
        score -= 0.30

    candidate.score = max(score, 0.0)
    return candidate
