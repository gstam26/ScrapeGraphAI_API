import re
from typing import Any

from rapidfuzz import fuzz

from models import ExtractedCell, SourceQuote

# token_sort_ratio threshold for near-duplicate value suppression in display output.
# Intentionally lower than AI_DEDUP_RATIO (95) in diagnostics/eval_lib/metrics.py:
# Matrix display collapses near-paraphrase duplicates; eval precision denom does not.
_DEDUP_RATIO = 85


def _has_value(cell: ExtractedCell) -> bool:
    """Check if cell has any value, including evidence-only values."""
    if cell.value not in (None, "", []):
        return True
    return bool(cell.evidence)


def _iter_values(value: Any) -> list[Any]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "", [])]
    return [value]


def _normalise_value(value: Any) -> str:
    return " ".join(str(value).strip().lower().split())


def _confidence_score(evidence: SourceQuote) -> float | None:
    if evidence.semantic_score is not None:
        return evidence.semantic_score
    return evidence.verification_score


def _copy_evidence_with_provenance(cell: ExtractedCell, evidence: SourceQuote) -> SourceQuote:
    copied = evidence.model_copy(deep=True)
    if not copied.source_url:
        copied.source_url = cell.source_url
    if not copied.page_title:
        copied.page_title = getattr(cell, "page_title", "") or ""
    if not copied.extraction_method:
        copied.extraction_method = getattr(cell, "extraction_method", "") or ""
    if copied.confidence_score is None:
        copied.confidence_score = _confidence_score(copied)
    return copied


def _evidence_from_cell_value(cell: ExtractedCell) -> list[SourceQuote]:
    return [
        SourceQuote(
            value=value,
            source_url=cell.source_url,
            page_title=getattr(cell, "page_title", "") or "",
            extraction_method=getattr(cell, "extraction_method", "") or "",
        )
        for value in _iter_values(cell.value)
    ]


_LIST_KEYWORDS = frozenset({"comma-separated", "deduplicated", "list", "for each"})

_NULL_SENTINEL_PREFIX = "none (not disclosed"


def _is_null_sentinel(normalised: str) -> bool:
    """True for 'None (not disclosed…)' values — explicit no-data markers, not competing answers."""
    return normalised.startswith(_NULL_SENTINEL_PREFIX)


def _is_list_column(instruction: str | None) -> bool:
    """Return True if the instruction signals a list-type (multi-value) answer."""
    if not instruction:
        return False
    text = instruction.lower()
    if any(kw in text for kw in _LIST_KEYWORDS):
        return True
    return bool(re.search(r"\bone\b.{1,30}\bper\b", text))


def _rank_evidence(evidence: list[SourceQuote]) -> list[SourceQuote]:
    """Sort evidence best-first: exact > fuzzy > none, then semantic_score descending."""
    _match_rank = {"exact": 0, "fuzzy": 1}

    def _key(ev: SourceQuote) -> tuple:
        rank = _match_rank.get(ev.match_type, 2)
        sem = ev.semantic_score if ev.semantic_score is not None else -1.0
        return (rank, -sem)

    return sorted(evidence, key=_key)


def aggregate_cells(
    cells: list[ExtractedCell],
    list_columns: set[str] | None = None,
) -> list[ExtractedCell]:
    """
    Group cells by entity and column, then collect all contributions.

    This first-pass aggregation does not choose a winner or synthesize a final
    answer. It deduplicates evidence by normalized value, quote, and source URL,
    keeps conflicting values, and stores cell.value as a list of unique values.

    list_columns: names of columns whose instructions signal a list-type answer.
    For list columns, multiple distinct values are expected and has_conflict is
    always False. Pass None to treat all columns as single-answer (conservative
    default, preserves old behaviour for callers without column metadata).
    """
    grouped: dict[tuple[str, str], list[ExtractedCell]] = {}
    for cell in cells:
        if not _has_value(cell):
            continue
        grouped.setdefault((cell.entity, cell.column), []).append(cell)

    _list_cols = list_columns or set()
    aggregated: list[ExtractedCell] = []
    for (entity, column), group in grouped.items():
        real_deduped: list[SourceQuote] = []      # genuine values
        sentinel_deduped: list[SourceQuote] = []  # "None (not disclosed…)" markers
        seen_evidence: set[tuple[str, str, str]] = set()
        unique_values: list[Any] = []   # real values only — used for conflict detection
        seen_value_norms: list[str] = []  # normalised strings for fuzzy dedup
        source_urls: set[str] = set()

        for cell in group:
            if cell.source_url:
                source_urls.add(cell.source_url)

            source_evidence = cell.evidence or _evidence_from_cell_value(cell)
            for evidence in source_evidence:
                copied = _copy_evidence_with_provenance(cell, evidence)
                if copied.value in (None, "", []):
                    continue

                value_key = _normalise_value(copied.value)
                evidence_key = (value_key, copied.quote or "", copied.source_url or "")
                if evidence_key in seen_evidence:
                    continue
                seen_evidence.add(evidence_key)

                if _is_null_sentinel(value_key):
                    # Sentinel: remember for fallback display but never count toward conflict.
                    sentinel_deduped.append(copied)
                else:
                    real_deduped.append(copied)
                    # Fuzzy dedup: suppress near-identical values (e.g. the same fact
                    # phrased slightly differently across multiple crawled pages) from
                    # the display list and conflict detector. Evidence is still kept for
                    # provenance. Uses the same threshold as the eval's AI_DEDUP_RATIO.
                    if not any(
                        fuzz.token_sort_ratio(value_key, s) >= _DEDUP_RATIO
                        for s in seen_value_norms
                    ):
                        seen_value_norms.append(value_key)
                        unique_values.append(copied.value)

        # When real values exist, suppress sentinels from evidence and display so the
        # Matrix shows only the real answer. When only sentinels exist, preserve them
        # so the cell shows "None (not disclosed on site)" rather than "No data found".
        if real_deduped:
            deduped_evidence = real_deduped
            display_values = unique_values
        else:
            deduped_evidence = sentinel_deduped
            display_values = [sentinel_deduped[0].value] if sentinel_deduped else []

        ranked_evidence = _rank_evidence(deduped_evidence)
        scores = [
            ev.verification_score
            for ev in ranked_evidence
            if ev.verification_score is not None
        ]
        sorted_urls = sorted(source_urls)
        aggregated.append(ExtractedCell(
            entity=entity,
            source_url="; ".join(sorted_urls),
            source_urls=sorted_urls,
            column=column,
            value=display_values,
            evidence=ranked_evidence,
            verified=bool(ranked_evidence) and all(ev.verified for ev in ranked_evidence),
            verification_score=sum(scores) / len(scores) if scores else None,
            has_conflict=(column not in _list_cols) and len(unique_values) > 1,
            num_sources=len(source_urls),
            num_unique_values=len(unique_values),
        ))

    return aggregated
