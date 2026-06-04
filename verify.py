from rapidfuzz import fuzz

from config import VERIFY_THRESHOLD
from models import PageDoc, ExtractedCell, SourceQuote


def _verify_quote(quote: str | None, page_text: str) -> tuple[bool, float | None]:
    """
    Verify a single quote against page text.
    
    Returns:
        (verified, score)
    """
    if not quote:
        return False, None

    score = fuzz.partial_ratio(quote.lower(), page_text.lower())
    score_float = float(score)
    verified = score >= VERIFY_THRESHOLD

    return verified, score_float


def verify_cell(cell: ExtractedCell, page: PageDoc) -> ExtractedCell:
    """
    Verify each evidence item independently against page text.
    
    Do NOT discard unverified evidence.
    Mark cell.verified = True only if ALL evidence items are verified.
    """
    if not cell.evidence:
        cell.verified = False
        cell.verification_score = None
        return cell

    # Verify each evidence item
    for evidence in cell.evidence:
        verified, score = _verify_quote(evidence.quote, page.text)
        evidence.verified = verified
        evidence.verification_score = score

    # Cell is verified only if ALL evidence items are verified
    all_verified = all(ev.verified for ev in cell.evidence if ev.quote)
    cell.verified = all_verified

    # Use average verification score across evidence
    scores = [ev.verification_score for ev in cell.evidence if ev.verification_score is not None]
    cell.verification_score = sum(scores) / len(scores) if scores else None

    return cell


def verify_cells(cells: list[ExtractedCell], page: PageDoc) -> list[ExtractedCell]:
    """Verify all cells against a page."""
    return [verify_cell(cell, page) for cell in cells]
