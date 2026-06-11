from rapidfuzz import fuzz

from config import VERIFY_THRESHOLD, VERIFY_TOOL
from models import ExtractedCell, PageDoc


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _verify_quote(quote: str | None, page_text: str) -> tuple[bool, float | None, tuple[int, int] | None, str]:
    if not quote:
        return False, None, None, "none"

    start = page_text.find(quote)
    if start >= 0:
        end = start + len(quote)
        return True, 100.0, (start, end), "exact"

    score = fuzz.partial_ratio(quote.lower(), page_text.lower())
    verified = score >= VERIFY_THRESHOLD
    match_type = "fuzzy" if verified else "none"
    return verified, float(score), None, match_type


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
        verified, score, char_span, match_type = _verify_quote(evidence.quote, page.text)
        evidence.verified = verified
        evidence.verification_score = score
        evidence.char_span = char_span
        evidence.match_type = match_type

        if diag is not None:
            diag.setdefault("verify_log", []).append({
                "entity": cell.entity,
                "source_url": cell.source_url,
                "question": cell.column,
                "claim_preview": str(evidence.value)[:150] if evidence.value is not None else "",
                "quote_preview": (evidence.quote or "")[:150],
                "verified": verified,
                "match_type": match_type,
                "verification_score": round(score, 1) if score is not None else "",
                "semantic_score": None,  # populated by verify_cells after batch embedding
                "verifier_tool": VERIFY_TOOL,
            })

    quoted_evidence = [ev for ev in cell.evidence if ev.quote]
    cell.verified = bool(quoted_evidence) and all(ev.verified for ev in quoted_evidence)

    scores = [ev.verification_score for ev in cell.evidence if ev.verification_score is not None]
    cell.verification_score = sum(scores) / len(scores) if scores else None

    return cell


def verify_cells(
    cells: list[ExtractedCell],
    page: PageDoc,
    entity: str | None = None,
    diag: dict | None = None,
) -> list[ExtractedCell]:
    """Verify all cells against a page, then add semantic similarity scores in one batch."""
    diag_start = len(diag.get("verify_log", [])) if diag is not None else 0
    result = [verify_cell(cell, page, entity=entity, diag=diag) for cell in cells]

    # Map each evidence item (value + quote both present) to its diag log index.
    eligible: list[tuple] = []
    diag_idx = diag_start
    for cell in result:
        for ev in cell.evidence:
            if ev.value is not None and ev.quote:
                eligible.append((ev, diag_idx if diag is not None else None))
            if diag is not None:
                diag_idx += 1

    if not eligible:
        return result

    texts: list[str] = []
    for ev, _ in eligible:
        texts.append(str(ev.value))
        texts.append(ev.quote)

    try:
        from src.embed import embed_batch
        vectors = embed_batch(texts)
        for i, (ev, d_idx) in enumerate(eligible):
            score = round(_cosine(vectors[2 * i], vectors[2 * i + 1]), 4)
            ev.semantic_score = score
            if d_idx is not None and diag is not None:
                diag["verify_log"][d_idx]["semantic_score"] = score
    except Exception as exc:
        print(f"      ! Semantic scoring unavailable: {exc}")

    return result
