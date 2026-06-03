from rapidfuzz import fuzz

from config import VERIFY_THRESHOLD
from models import PageDoc, ExtractedCell


def verify_cell(cell: ExtractedCell, page: PageDoc) -> ExtractedCell:
    if not cell.quote:
        cell.verified = False
        cell.verification_score = None
        return cell

    score = fuzz.partial_ratio(cell.quote.lower(), page.text.lower())

    cell.verification_score = float(score)
    cell.verified = score >= VERIFY_THRESHOLD

    return cell


def verify_cells(cells: list[ExtractedCell], page: PageDoc) -> list[ExtractedCell]:
    return [verify_cell(cell, page) for cell in cells]