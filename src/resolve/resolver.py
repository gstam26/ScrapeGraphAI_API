"""Resolver orchestration: CompanyInput -> ResolutionResult.

Flow per company:
  1. Search for candidate URLs via Firecrawl search only (no direct-internet
     fallback — see search.py for why).
  2. Collapse candidates to one-per-registrable-domain (keep the best-ranked).
  3. Optionally fetch the homepage of the top few non-blocked candidates to
     enrich the (offline) content score with real page text.
  4. Score every candidate, drop blocklisted domains from selection.
  5. Pick the best as resolved_url; the next-best non-blocked domains become
     candidate_alternatives. Decide needs_review and assemble notes.

Homepage fetching reuses the acquire layer's fetch_page_with_provenance and is
best-effort: any fetch failure just leaves that candidate scored on its
title/snippet. Embeddings are optional. If Firecrawl search itself fails or
returns nothing, the company is reported unresolved with needs_review=True —
nothing is guessed via an unvetted search path.
"""

from src.resolve import search as search_mod
from src.resolve.confidence import (
    decide_review,
    registrable_domain,
    score_candidate,
)
from src.resolve.io_csv import read_input_csv, write_output_csv
from src.resolve.models import Candidate, CompanyInput, ResolutionResult

# How many alternative URLs to report alongside the resolved one.
MAX_ALTERNATIVES = 3
# How many top candidates to enrich with a homepage fetch (cost control).
ENRICH_TOP_N = 3


def _dedupe_by_domain(candidates: list[Candidate]) -> list[Candidate]:
    """Keep one candidate per registrable domain — the earliest-ranked."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in candidates:
        dom = c.domain or registrable_domain(c.url)
        c.domain = dom
        if not dom or dom in seen:
            continue
        seen.add(dom)
        out.append(c)
    return out


def _fetch_homepage_text(url: str, cfg) -> str:
    """Best-effort homepage fetch via the acquire layer. Returns "" on failure."""
    try:
        from src.acquire.fetcher import fetch_page_with_provenance

        text, _html, _prov = fetch_page_with_provenance(url, cfg)
        return text or ""
    except Exception:
        return ""


def resolve_company(
    inp: CompanyInput,
    *,
    cfg=None,
    limit: int = search_mod.DEFAULT_LIMIT,
    fetch_homepages: bool = True,
    use_embeddings: bool = False,
) -> ResolutionResult:
    """Resolve a single company record to its official URL."""
    candidates = search_mod.search_company(
        inp.company, inp.description, inp.categories, limit=limit
    )
    search_backend = search_mod.last_search_backend

    if not candidates:
        return ResolutionResult(
            company=inp.company,
            needs_review=True,
            notes=f"no search results ({search_backend})",
        )

    candidates = _dedupe_by_domain(candidates)

    # Optionally enrich the top non-blocked candidates with homepage text.
    enriched: dict[str, str] = {}
    if fetch_homepages and cfg is not None:
        from src.resolve.confidence import is_blocked

        enrich_targets = [c for c in candidates if not is_blocked(c.domain)][:ENRICH_TOP_N]
        for c in enrich_targets:
            enriched[c.url] = _fetch_homepage_text(c.url, cfg)

    for c in candidates:
        score_candidate(
            c,
            inp,
            page_text=enriched.get(c.url, ""),
            use_embeddings=use_embeddings,
        )

    eligible = sorted(
        (c for c in candidates if not c.blocked),
        key=lambda c: c.confidence,
        reverse=True,
    )

    if not eligible:
        blocked_domains = ", ".join(sorted({c.domain for c in candidates if c.blocked}))
        return ResolutionResult(
            company=inp.company,
            needs_review=True,
            notes=f"all candidates blocklisted ({blocked_domains}) [{search_backend}]",
        )

    best = eligible[0]
    alternatives = [c.url for c in eligible[1 : 1 + MAX_ALTERNATIVES]]
    needs_review, review_note = decide_review(eligible)

    return ResolutionResult(
        company=inp.company,
        resolved_url=best.url,
        confidence=best.confidence,
        candidate_alternatives=alternatives,
        needs_review=needs_review,
        notes=f"{review_note} [{search_backend}]",
    )


def resolve_csv(
    input_path: str,
    output_path: str = "resolved_urls.csv",
    *,
    cfg=None,
    limit: int = search_mod.DEFAULT_LIMIT,
    fetch_homepages: bool = True,
    use_embeddings: bool = False,
) -> list[ResolutionResult]:
    """Resolve every row of an input CSV and write the output CSV."""
    rows = read_input_csv(input_path)
    results: list[ResolutionResult] = []
    for i, inp in enumerate(rows, 1):
        print(f"  [{i}/{len(rows)}] {inp.company}")
        results.append(
            resolve_company(
                inp,
                cfg=cfg,
                limit=limit,
                fetch_homepages=fetch_homepages,
                use_embeddings=use_embeddings,
            )
        )
    write_output_csv(output_path, results)
    return results
