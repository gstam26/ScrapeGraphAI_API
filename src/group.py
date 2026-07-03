"""
Deterministic grouping of verified claims within aggregated Matrix cells.

Motivation (brain/proposals/filter-and-synthesis.md, Part 2a): a validation
cell held ~654 news items — useless raw. This layer clusters the claims in
each aggregated cell into themes so consultants see structure, not a wall of
bullets. Hard constraints honoured here:

  * NO LLM anywhere — embeddings (Ollama nomic-embed) + fixed-threshold
    greedy agglomerative clustering only.
  * Fully deterministic given embeddings: values are iterated in sorted()
    order, assigned to the FIRST existing cluster whose centroid clears
    GROUP_SIMILARITY, and theme labels are medoid MEMBER strings (real
    verified claims, never synthesized text).
  * The Matrix / Provenance / aggregate.py chain is untouched — this layer
    only READS the aggregated rows and emits extra sheet rows.
  * Degrades gracefully off-network: on embedding failure group_rows raises
    one clean RuntimeError; the pipeline wraps the call in try/except and
    simply skips the sheet (see run_pipeline).
"""
import math

from config import GROUP_MIN_ITEMS, GROUP_SIMILARITY, OLLAMA_DOC_PREFIX
from models import ExtractedCell, ExtractedRow
from src.embed import embed_batch

# Theme label for cells too small to be worth clustering.
ALL_ITEMS_THEME = "(all items)"

# Mirrors aggregate.py's null-sentinel convention — "None (not disclosed…)"
# markers are explicit no-data values, not claims worth grouping.
_NULL_SENTINEL_PREFIX = "none (not disclosed"


def _cosine(a: list[float], b: list[float]) -> float:
    # Same helper as src/filter.py _cosine.
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _normalise_value(value) -> str:
    # Mirrors aggregate.py's _normalise_value so source matching stays aligned.
    return " ".join(str(value).strip().lower().split())


def _is_null_sentinel(normalised: str) -> bool:
    return normalised.startswith(_NULL_SENTINEL_PREFIX)


def _display_values(cell: ExtractedCell) -> list[str]:
    """Distinct display values of an aggregated cell, in original display order.

    cell.value is a list of deduped value strings for list cells; scalars are
    normalised to a 1-item list. Empty values and null sentinels are dropped;
    exact (normalised) duplicates keep their first occurrence.
    """
    raw = cell.value
    if isinstance(raw, list):
        candidates = [v for v in raw if v not in (None, "", [])]
    elif raw not in (None, "", []):
        candidates = [raw]
    else:
        candidates = []

    values: list[str] = []
    seen: set[str] = set()
    for v in candidates:
        text = str(v).strip()
        norm = _normalise_value(text)
        if not text or _is_null_sentinel(norm) or norm in seen:
            continue
        seen.add(norm)
        values.append(text)
    return values


def cluster_values(
    values: list[str],
    vectors: dict[str, list[float]],
    threshold: float = GROUP_SIMILARITY,
) -> list[list[str]]:
    """Greedy deterministic agglomerative clustering of one cell's values.

    Iterate values in sorted() order; assign each to the FIRST existing
    cluster whose CENTROID cosine >= threshold, else start a new cluster;
    update the centroid incrementally (mean of member vectors). Sorted
    iteration + first-match assignment + fixed threshold = deterministic
    given embeddings.

    vectors maps value string -> embedding. Returns clusters as lists of the
    value strings, in cluster-creation order (members in sorted order).
    """
    clusters: list[list[str]] = []
    centroids: list[list[float]] = []
    counts: list[int] = []

    for value in sorted(values):
        vec = vectors[value]
        assigned = False
        for i, centroid in enumerate(centroids):
            if _cosine(vec, centroid) >= threshold:
                clusters[i].append(value)
                n = counts[i]
                centroids[i] = [(c * n + x) / (n + 1) for c, x in zip(centroid, vec)]
                counts[i] = n + 1
                assigned = True
                break
        if not assigned:
            clusters.append([value])
            centroids.append(list(vec))
            counts.append(1)

    return clusters


def _medoid(members: list[str], vectors: dict[str, list[float]]) -> str:
    """Medoid member: max mean cosine to the other members.

    For 1-2 members the first in sorted order. Ties broken by sorted order
    (members arrive in sorted order from cluster_values). Always a real
    verified claim string — never synthesized text.
    """
    if len(members) <= 2:
        return min(members)
    best_value = members[0]
    best_score = -2.0
    for m in members:
        score = sum(_cosine(vectors[m], vectors[o]) for o in members if o is not m)
        score /= len(members) - 1
        if score > best_score:
            best_score = score
            best_value = m
    return best_value


def _distinct_sources(cell: ExtractedCell, members: list[str]) -> int:
    """Count distinct source_urls among the cell evidence whose value is in
    this cluster (matched on normalised value string; unmatched values are
    simply omitted from the count)."""
    member_norms = {_normalise_value(m) for m in members}
    urls: set[str] = set()
    for ev in cell.evidence:
        if ev.value is None:
            continue
        if _normalise_value(ev.value) in member_norms and ev.source_url:
            urls.add(ev.source_url)
    return len(urls)


def group_rows(rows: list[ExtractedRow]) -> list[dict]:
    """Group the claims inside each aggregated cell into deterministic themes.

    Returns one dict per theme:
      {"entity", "question", "theme", "n_items", "values", "sources"}
    with "values" holding the member claims in original cell display order.

    Cells with fewer than GROUP_MIN_ITEMS distinct values become ONE
    "(all items)" group (no embedding needed). Larger cells are clustered on
    embeddings fetched in ONE embed_batch call for the whole run.

    Raises RuntimeError on embedding failure — run_pipeline wraps this call
    in try/except so a failed/unreachable Ollama only skips the sheet and can
    never fail the run.
    """
    # Pass 1 — collect display values per cell; batch every value that needs
    # an embedding across all cells (one network call for the whole run).
    cell_entries: list[tuple[ExtractedRow, ExtractedCell, list[str]]] = []
    to_embed: list[str] = []
    for row in rows:
        for cell in row.cells:
            values = _display_values(cell)
            if not values:
                continue
            cell_entries.append((row, cell, values))
            if len(values) >= GROUP_MIN_ITEMS:
                to_embed.extend(values)

    vectors_flat: list[list[float]] = []
    if to_embed:
        try:
            vectors_flat = embed_batch([OLLAMA_DOC_PREFIX + v for v in to_embed])
        except Exception as exc:
            raise RuntimeError(f"claim-grouping embedding failed: {exc}") from exc

    # Pass 2 — slice vectors per cell and cluster.
    out: list[dict] = []
    offset = 0
    for row, cell, values in cell_entries:
        if len(values) < GROUP_MIN_ITEMS:
            out.append({
                "entity": row.entity,
                "question": cell.column,
                "theme": ALL_ITEMS_THEME,
                "n_items": len(values),
                "values": list(values),
                "sources": _distinct_sources(cell, values),
            })
            continue

        vectors = {v: vectors_flat[offset + i] for i, v in enumerate(values)}
        offset += len(values)

        clusters = cluster_values(values, vectors)
        display_order = {v: i for i, v in enumerate(values)}
        themed = [
            {
                "entity": row.entity,
                "question": cell.column,
                "theme": _medoid(members, vectors),
                "n_items": len(members),
                "values": sorted(members, key=display_order.__getitem__),
                "sources": _distinct_sources(cell, members),
            }
            for members in clusters
        ]
        themed.sort(key=lambda g: (-g["n_items"], g["theme"]))
        out.extend(themed)

    return out
