"""Confidence scoring for company → URL resolution.

Design (per project decision): the core is FULLY OFFLINE.

  1. name_score   — company-name ↔ domain similarity via rapidfuzz.
  2. content_score — weighted keyword coverage of the company name +
                     conference description + categories against the
                     candidate's page text (title/snippet/homepage). This is
                     the "keyword/BM25-style" corroboration signal.
  3. rank_score   — a prior from the search-result rank.
  4. embed_boost  — OPTIONAL agreement from Ollama embeddings, added only when
                    the embed endpoint is reachable. It can never reduce a
                    score and is never a hard dependency (the Ollama server is
                    Sagentia-VPN-only; off-VPN this silently contributes 0).

  Negative signal: aggregator / social / directory / event-listing domains are
  disqualified via an explicit, easily-extended blocklist (BLOCKLIST_DOMAINS).

The final confidence is a weighted blend of (1)-(3), plus the optional boost,
minus penalties, clamped to 0..1. All weights/thresholds are module constants.
"""

import math
import re
from urllib.parse import urlparse

from rapidfuzz import fuzz

from src.resolve.models import Candidate, CompanyInput


# ============================================================
# Blocklist — aggregators / social / directories / event listings.
# Module-level and intentionally easy to extend: add a registrable domain
# (or a bare brand token, matched as a label) to disqualify it as an
# "official" company URL. Matching is done on the registrable domain and on
# individual host labels, so "linkedin" catches linkedin.com, uk.linkedin.com,
# etc.
# ============================================================

BLOCKLIST_DOMAINS: set[str] = {
    # social
    "linkedin.com",
    "facebook.com",
    "fb.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "medium.com",
    # company directories / data aggregators
    "crunchbase.com",
    "wikipedia.org",
    "bloomberg.com",
    "dnb.com",
    "zoominfo.com",
    "glassdoor.com",
    "indeed.com",
    "owler.com",
    "pitchbook.com",
    "opencorporates.com",
    # marketplaces / retail aggregators
    "amazon.com",
    "alibaba.com",
    "ebay.com",
    "etsy.com",
    # research / publication aggregators
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "researchgate.net",
    "scholar.google.com",
    "semanticscholar.org",
    # conference / event-listing platforms (not the exhibitor's own site)
    "eventbrite.com",
    "10times.com",
    "expodatabase.com",
    "tradefairdates.com",
    "eventseye.com",
    "n200.com",
    "swapcard.com",
    "mapyourshow.com",
}

# Bare host labels that, on their own, indicate a non-official host even when
# the full registrable domain is not enumerated above (e.g. country variants).
_BLOCKLIST_LABELS: set[str] = {dom.split(".")[0] for dom in BLOCKLIST_DOMAINS}


# ============================================================
# Scoring weights and thresholds (tunable in one place).
# ============================================================

NAME_WEIGHT = 0.50      # company-name ↔ domain match
CONTENT_WEIGHT = 0.35   # name+description+categories ↔ page text
RANK_WEIGHT = 0.15      # search-rank prior
EMBED_BOOST_MAX = 0.10  # ceiling for the optional embedding agreement boost

# Inside content_score, how the two sub-signals are weighted.
CONTENT_NAME_IN_TEXT_WEIGHT = 0.55   # is the company name present in the text?
CONTENT_TOPIC_WEIGHT = 0.45          # does the description/categories topic match?

# Token weights for keyword coverage: company tokens matter more than the
# softer description/category context.
COMPANY_TOKEN_WEIGHT = 2.0
CONTEXT_TOKEN_WEIGHT = 1.0

REVIEW_THRESHOLD = 0.60   # below this, flag needs_review
AMBIGUITY_MARGIN = 0.08   # top two this close (and both decent) => ambiguous
AMBIGUITY_FLOOR = 0.45    # only call it ambiguous if the runner-up is at least this strong

# Legal-entity suffixes stripped before name↔domain comparison.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "plc",
    "co", "corp", "corporation", "company", "gmbh", "ag", "kg", "sa", "sas",
    "srl", "spa", "bv", "nv", "oy", "ab", "as", "pty", "group", "holding",
    "holdings", "international", "intl", "global", "worldwide", "technologies",
    "technology", "tech", "solutions", "systems", "labs", "laboratories",
}

_STOP = frozenset({
    "the", "and", "or", "of", "in", "to", "for", "with", "on", "at", "by",
    "an", "as", "is", "are", "be", "not", "from", "that", "this", "it", "its",
    "we", "our", "your", "their", "you", "they", "a", "our", "all", "more",
})

# Common multi-part public suffixes, so we extract the registrable domain
# (eTLD+1) without pulling in a Public Suffix List dependency. This is a
# pragmatic subset, not the full PSL; unknown multi-part suffixes fall back to
# the last two labels.
_MULTI_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "co.za", "co.in", "co.jp", "co.kr", "co.il", "co.id", "co.th",
    "com.br", "com.cn", "com.mx", "com.tr", "com.sg", "com.hk", "com.tw",
    "com.my", "com.ar", "com.co", "com.ua", "com.ph", "com.sa", "com.eg",
    "or.jp", "ne.jp", "go.jp",
}


# ============================================================
# Domain / name helpers
# ============================================================

def registrable_domain(url: str) -> str:
    """Return the registrable domain (eTLD+1, heuristic) for a URL.

    Strips scheme, userinfo, port, and a leading 'www.'. Uses a known set of
    multi-part public suffixes; otherwise falls back to the last two labels.
    Returns "" if no host can be parsed.
    """
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower().strip()
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    labels = [l for l in host.split(".") if l]
    if len(labels) <= 2:
        return ".".join(labels)
    last_two = ".".join(labels[-2:])
    last_three = ".".join(labels[-3:])
    if last_two in _MULTI_PART_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    # guard against e.g. "foo.co.uk" where last_three would over-capture
    if last_three.split(".", 1)[-1] in _MULTI_PART_SUFFIXES:
        return last_three
    return last_two


def _domain_sld(domain: str) -> str:
    """The brandable label of a registrable domain (everything before the
    public suffix). E.g. 'acme.co.uk' -> 'acme', 'acme.com' -> 'acme'."""
    if not domain:
        return ""
    labels = domain.split(".")
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_PART_SUFFIXES and len(labels) >= 3:
        return labels[-3]
    return labels[0]


def is_blocked(domain: str) -> bool:
    """True if the registrable domain is an aggregator/social/directory host."""
    if not domain:
        return False
    if domain in BLOCKLIST_DOMAINS:
        return True
    labels = set(domain.split("."))
    return bool(labels & _BLOCKLIST_LABELS)


def _normalise_name(name: str) -> list[str]:
    """Lowercase, drop punctuation and legal suffixes, return content tokens."""
    raw = re.findall(r"[a-z0-9]+", (name or "").lower())
    tokens = [t for t in raw if t not in _LEGAL_SUFFIXES]
    return tokens or raw  # if stripping removed everything, keep the raw tokens


def _tokenize(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) >= 2 and w not in _STOP
    ]


# ============================================================
# Component scores
# ============================================================

def score_name(company: str, domain: str) -> tuple[float, str]:
    """Similarity between the company name and the domain's brand label.

    Returns (score in 0..1, short reason). Uses several rapidfuzz views and a
    containment check so that 'Acme Robotics Inc' ↔ 'acmerobotics.com' scores
    high while unrelated domains score low.
    """
    sld = _domain_sld(domain)
    name_tokens = _normalise_name(company)
    if not sld or not name_tokens:
        return 0.0, "no name/domain to compare"

    name_spaced = " ".join(name_tokens)
    name_compact = "".join(name_tokens)
    sld_spaced = re.sub(r"[-_]+", " ", sld)
    sld_compact = re.sub(r"[^a-z0-9]+", "", sld)

    token_set = fuzz.token_set_ratio(name_spaced, sld_spaced) / 100.0
    compact_ratio = fuzz.ratio(name_compact, sld_compact) / 100.0
    partial = fuzz.partial_ratio(name_compact, sld_compact) / 100.0

    # Strong containment: the domain label literally contains the compacted
    # company name (or vice-versa for short brands).
    contained = 0.0
    if name_compact and (name_compact in sld_compact or sld_compact in name_compact):
        shorter = min(len(name_compact), len(sld_compact))
        longer = max(len(name_compact), len(sld_compact))
        contained = shorter / longer if longer else 0.0

    score = max(token_set, compact_ratio, contained, 0.85 * partial)
    reason = (
        f"name~domain={score:.2f} "
        f"(set={token_set:.2f},compact={compact_ratio:.2f},contain={contained:.2f})"
    )
    return min(score, 1.0), reason


def _keyword_coverage(query_weights: dict[str, float], text_tokens: set[str]) -> float:
    """Weighted fraction of query tokens present in the text (0..1)."""
    total = sum(query_weights.values())
    if total <= 0:
        return 0.0
    hit = sum(w for tok, w in query_weights.items() if tok in text_tokens)
    return hit / total


def score_content(inp: CompanyInput, text: str) -> tuple[float, str]:
    """Corroboration between the input record and a candidate's page text.

    Two sub-signals, both offline:
      - name_in_text : is the company name present in the text (rapidfuzz)?
      - topic_match  : weighted keyword coverage of company + description +
                       category tokens in the text.
    """
    if not text:
        return 0.0, "no candidate text"

    text_tokens = set(_tokenize(text))
    company_tokens = _normalise_name(inp.company)

    # name_in_text: best fuzzy match of the whole company name within the text.
    name_in_text = fuzz.partial_ratio(
        " ".join(company_tokens), text.lower()
    ) / 100.0 if company_tokens else 0.0

    # topic_match: weighted keyword coverage.
    query_weights: dict[str, float] = {}
    for tok in company_tokens:
        query_weights[tok] = max(query_weights.get(tok, 0.0), COMPANY_TOKEN_WEIGHT)
    for tok in _tokenize(f"{inp.description} {inp.categories}"):
        query_weights.setdefault(tok, CONTEXT_TOKEN_WEIGHT)
    topic_match = _keyword_coverage(query_weights, text_tokens)

    score = (
        CONTENT_NAME_IN_TEXT_WEIGHT * name_in_text
        + CONTENT_TOPIC_WEIGHT * topic_match
    )
    reason = f"content={score:.2f} (name_in_text={name_in_text:.2f},topic={topic_match:.2f})"
    return min(score, 1.0), reason


def score_rank(rank: int) -> float:
    """Search-rank prior: rank 0 -> 1.0, decaying with position."""
    return 1.0 / (1.0 + max(rank, 0))


def embed_agreement(query_text: str, candidate_text: str) -> float:
    """OPTIONAL boost: cosine agreement from Ollama embeddings, scaled to
    EMBED_BOOST_MAX. Returns 0.0 on any failure (endpoint unreachable, bad
    response, missing dependency) — never raises, never a hard dependency.
    """
    if not query_text.strip() or not candidate_text.strip():
        return 0.0
    try:
        from src.embed import embed_batch  # local import: optional path only

        vecs = embed_batch([query_text, candidate_text])
        if len(vecs) != 2:
            return 0.0
        a, b = vecs
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        cos = dot / (na * nb) if na and nb else 0.0
        return max(0.0, min(cos, 1.0)) * EMBED_BOOST_MAX
    except Exception:
        return 0.0


# ============================================================
# Candidate scoring + selection
# ============================================================

def score_candidate(
    candidate: Candidate,
    inp: CompanyInput,
    *,
    page_text: str = "",
    use_embeddings: bool = False,
) -> Candidate:
    """Populate a candidate's score components and final confidence in place.

    `page_text` is optional richer text (e.g. fetched homepage content). When
    absent, the candidate's own title+snippet are used for content scoring.
    """
    candidate.domain = candidate.domain or registrable_domain(candidate.url)
    candidate.blocked = is_blocked(candidate.domain)

    text_for_content = page_text or f"{candidate.title} {candidate.snippet}"

    name_score, name_reason = score_name(inp.company, candidate.domain)
    content_score, content_reason = score_content(inp, text_for_content)
    rank_score = score_rank(candidate.rank)

    boost = 0.0
    if use_embeddings:
        query_text = f"{inp.company} {inp.description} {inp.categories}".strip()
        boost = embed_agreement(query_text, text_for_content)

    penalty = 1.0 if candidate.blocked else 0.0

    confidence = (
        NAME_WEIGHT * name_score
        + CONTENT_WEIGHT * content_score
        + RANK_WEIGHT * rank_score
        + boost
        - penalty
    )

    candidate.name_score = round(name_score, 4)
    candidate.content_score = round(content_score, 4)
    candidate.rank_score = round(rank_score, 4)
    candidate.embed_boost = round(boost, 4)
    candidate.penalty = round(penalty, 4)
    candidate.confidence = round(max(0.0, min(confidence, 1.0)), 4)

    reasons = [name_reason, content_reason, f"rank={rank_score:.2f}"]
    if boost:
        reasons.append(f"embed_boost=+{boost:.2f}")
    if candidate.blocked:
        reasons.append("BLOCKED:aggregator/social/directory")
    candidate.reasons = reasons
    return candidate


def decide_review(ranked: list[Candidate]) -> tuple[bool, str]:
    """Given candidates sorted best-first (blocked already excluded), decide the
    needs_review flag and a human-readable note.
    """
    if not ranked:
        return True, "no eligible candidates found"

    best = ranked[0]
    notes: list[str] = []
    needs_review = False

    if best.confidence < REVIEW_THRESHOLD:
        needs_review = True
        notes.append(f"low confidence {best.confidence:.2f} < {REVIEW_THRESHOLD:.2f}")

    if len(ranked) > 1:
        runner = ranked[1]
        if (
            runner.confidence >= AMBIGUITY_FLOOR
            and (best.confidence - runner.confidence) < AMBIGUITY_MARGIN
        ):
            needs_review = True
            notes.append(
                f"ambiguous: {best.domain} ({best.confidence:.2f}) vs "
                f"{runner.domain} ({runner.confidence:.2f})"
            )

    if not needs_review:
        notes.append(f"confident: {best.domain} @ {best.confidence:.2f}")

    # Always surface the winning signal breakdown for traceability.
    notes.append("; ".join(best.reasons))
    return needs_review, " | ".join(notes)
