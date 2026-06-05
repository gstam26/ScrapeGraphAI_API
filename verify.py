from rapidfuzz import fuzz

from config import VERIFY_THRESHOLD, VERIFY_TOOL
from models import ExtractedCell, PageDoc


def _verify_quote(quote: str | None, page_text: str) -> tuple[bool, float | None]:
    if not quote:
        return False, None
    score = fuzz.partial_ratio(quote.lower(), page_text.lower())
    return score >= VERIFY_THRESHOLD, float(score)


def _match_type(verified: bool, score: float | None) -> str:
    if score is None:
        return "none"
    if score >= 100:
        return "exact"
    if verified:
        return "fuzzy"
    return "none"


def verify_cell(
    cell: ExtractedCell,
    page: PageDoc,
    entity: str | None = None,
    diag: dict | None = None,
) -> ExtractedCell:
    """
    Verify each evidence item independently against page text.

    Do not discard unverified evidence. Mark cell.verified = True only if all
    evidence items with quotes are verified.
    """
    if entity and not cell.entity:
        cell.entity = entity

    if not cell.evidence:
        cell.verified = False
        cell.verification_score = None
        return cell

    for evidence in cell.evidence:
        verified, score = _verify_quote(evidence.quote, page.text)
        evidence.verified = verified
        evidence.verification_score = score

        if diag is not None:
            diag.setdefault("verify_log", []).append({
                "entity": cell.entity,
                "source_url": cell.source_url,
                "question": cell.column,
                "claim_preview": str(evidence.value)[:150] if evidence.value is not None else "",
                "quote_preview": (evidence.quote or "")[:150],
                "verified": verified,
                "match_type": _match_type(verified, score),
                "verification_score": round(score, 1) if score is not None else "",
                "verifier_tool": VERIFY_TOOL,
            })

    all_verified = all(ev.verified for ev in cell.evidence if ev.quote)
    cell.verified = all_verified

    scores = [ev.verification_score for ev in cell.evidence if ev.verification_score is not None]
    cell.verification_score = sum(scores) / len(scores) if scores else None

    return cell


def verify_cells(
    cells: list[ExtractedCell],
    page: PageDoc,
    entity: str | None = None,
    diag: dict | None = None,
) -> list[ExtractedCell]:
    """Verify all cells against a page."""
    return [verify_cell(cell, page, entity=entity, diag=diag) for cell in cells]
