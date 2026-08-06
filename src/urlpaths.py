"""URL path classification, shared by Acquire and Extract.

A leaf module by design: both layers need the same judgement about a URL, and
neither may import the other (the layer-separation rule — layers are wired by
config dispatch, not by knowing about each other).

Two categories, deliberately distinct:

  boilerplate — legal/compliance pages (privacy policy, Impressum, terms).
    NOT blocked from the crawl. They carry the registered company name and
    address BY LAW (GDPR Art. 13 requires the controller's identity up front;
    a German Impressum is nothing but that address), which makes them among
    the most reliable pages on a site for HQ / legal-entity questions. On a
    link-starvation diagnosis run, Arrk's verified "Osaka, Japan" HQ came from
    jp.arrk.com/company/en/privacypolicy and from no other page the crawl
    reached. Classified so Extract can cap their cost instead of paying 30
    Azure calls for a 234K-char privacy policy (Sanmina).

  blocked — infrastructure endpoints and template artefacts that are not
    pages at all. Safe to drop outright at link discovery.

Matching is a FULLMATCH against individual path segments with the page
extension stripped — never a substring scan. That is what keeps this from
becoming the brittle blocklist rejected earlier in the project:
/privacy-first-manufacturing, /news/cookie-factory-opens and /glossary/terms-of-the-trade
all survive, while /privacypolicy and /en/cookie-policy.html classify.
Topical segments (/products/, /services/) must never be added here — whether
those are boilerplate is domain knowledge that does not generalise, and it
belongs to the embedding page-type signal.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from config import BLOCKED_PATH_PATTERNS, BOILERPLATE_PATH_PATTERNS

_PAGE_EXT_RE = re.compile(r"\.(html?|php|aspx?|jsp|cfm)$", re.IGNORECASE)
_BOILERPLATE_SEG_RE = re.compile(
    "|".join(f"(?:{p})" for p in BOILERPLATE_PATH_PATTERNS), re.IGNORECASE
)
_BLOCKED_SEG_RE = re.compile(
    "|".join(f"(?:{p})" for p in BLOCKED_PATH_PATTERNS), re.IGNORECASE
)


def path_segments(url: str) -> list[str]:
    """Path segments with any page extension stripped."""
    return [
        _PAGE_EXT_RE.sub("", seg)
        for seg in urlparse(url).path.split("/")
        if seg
    ]


def is_boilerplate_path(url: str) -> bool:
    """True if any path segment is a legal/compliance page.

    Flag-independent: the classification is a fact about the URL. Whether
    anything is DONE about it is each layer's own config decision
    (EXTRACT_MAX_CHUNKS_BOILERPLATE in Extract).
    """
    return any(_BOILERPLATE_SEG_RE.fullmatch(seg) for seg in path_segments(url))


def is_blocked_path(url: str) -> bool:
    """True if the URL is an infrastructure endpoint or template artefact."""
    return any(_BLOCKED_SEG_RE.fullmatch(seg) for seg in path_segments(url))
