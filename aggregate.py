from models import ExtractedCell


def _has_value(cell: ExtractedCell) -> bool:
    """Check if cell has any value, including evidence-only values."""
    if cell.value not in (None, "", []):
        return True

    # Even if value is null, if we have evidence, keep it
    if cell.evidence:
        return True

    return False


def aggregate_cells(cells: list[ExtractedCell]) -> list[ExtractedCell]:
    """
    Group cells by column and select the best one.
    
    Selection logic:
    - Prefer cells with values over evidence-only
    - Prefer verified cells
    - Prefer higher verification score
    
    Do NOT drop evidence-only cells.
    Preserve all evidence in the final cell.
    """
    best_by_column: dict[str, ExtractedCell] = {}

    for cell in cells:
        if not _has_value(cell):
            continue

        column = cell.column
        existing = best_by_column.get(column)

        if existing is None:
            best_by_column[column] = cell
            continue

        # Decide if new cell is better than existing
        new_has_value = cell.value not in (None, "", [])
        existing_has_value = existing.value not in (None, "", [])

        # Prefer cell with value over evidence-only
        if new_has_value and not existing_has_value:
            best_by_column[column] = cell
            continue

        if not new_has_value and existing_has_value:
            continue

        # Both have values or both evidence-only
        # Prefer verified
        new_verified = cell.verified
        existing_verified = existing.verified

        if new_verified and not existing_verified:
            best_by_column[column] = cell
            continue

        if not new_verified and existing_verified:
            continue

        # Same verification status: prefer higher score
        existing_score = existing.verification_score or 0
        new_score = cell.verification_score or 0

        if new_score > existing_score:
            best_by_column[column] = cell

    return list(best_by_column.values())
