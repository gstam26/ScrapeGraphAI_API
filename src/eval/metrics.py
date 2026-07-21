"""
Metrics for the extraction evaluation framework (Stage 10 / RQ3).

Consumes the aligner's AlignmentResult and computes recall / precision / F1 and the
ai_only taxonomy, per (entity, question) cell, then macro-aggregated overall, by
question, by entity, and by type/dimension tag. No embedding or matching happens
here — that is the aligner's job; this module only counts.

Key methodology (confirmed decisions):

  Recall   : two bounds. recall_auto counts only auto_match + null_match; recall_full
             additionally counts manual-band rows (optimistic upper bound until the
             analyst resolves them). True recall lands between the two.

  Precision: REPORT BOTH (confirmed). AI claims are first collapsed by deterministic
             near-duplicate rule (rapidfuzz token_sort_ratio >= AI_DEDUP_RATIO).
               precision_strict   = TP / |distinct AI claims|
               precision_distinct = TP / (|distinct AI| - redundant - dynamic_neutral)
             The strict/distinct gap quantifies pipeline redundancy.

  ai_only taxonomy (each distinct unmatched AI claim):
     redundant_restatement : nearest GT (combined >= AUTO_MATCH_THRESHOLD) is already
                             matched -> a correct re-statement of a recalled fact.
                             Dropped from the distinct-precision denominator.
     out_of_scope_fp       : matches an inclusion_bar Excluded statement -> false positive.
     dynamic_neutral       : matches a dynamic_counter Excluded statement -> dropped
                             from BOTH precision denominators (neither TP nor FP).
     possible_gt_gap       : verified on-page claim with no GT near it -> the GROUND
                             TRUTH may be incomplete (prominent finding, Decision 6).
                             Stays in the precision denominator but is surfaced.
     hallucination         : not source-verified and no GT near -> true error.

  Localisation (metric 2.7) is intentionally OMITTED: char_span is absent from the
  Excel Provenance sheet. Recorded as a known limitation, not scored as 0.

Run directly (needs Ollama for the Sustainability cosine path, like the aligner):
    python src/eval/metrics.py <ground_truth.xlsx> <pipeline_output.xlsx>
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

# --- repo-root bootstrap -----------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rapidfuzz import fuzz

from src.eval.aligner import (
    AUTO_MATCH_THRESHOLD,
    AlignmentResult,
    CellAlignment,
    align,
)
from src.eval.gt_reader import GroundTruth, GTClaim, read_ground_truth
from src.eval.pipeline_reader import AIClaim, read_pipeline_output


# =====================================================================
# Config constants (provisional — tune alongside the aligner's).
# =====================================================================
AI_DEDUP_RATIO = 95        # token_sort_ratio >= this => same AI claim (precision denom)
EXCLUDED_MATCH_RATIO = 85  # token_set_ratio >= this => AI claim matches an Excluded statement

# Verdicts that count as a recall/precision match.
_MATCHED_AUTO = frozenset({"auto_match", "null_match"})
_MATCHED_FULL = frozenset({"auto_match", "null_match", "manual"})


# --- result model ------------------------------------------------------------
@dataclass
class CellMetrics:
    entity: str
    question: str
    question_type: str            # "list" | "single"

    gt_active: int                # recall denominator
    ai_raw: int                   # AI claims before dedup
    ai_distinct: int              # AI claims after token_sort dedup
    tp: int                       # distinct AI claims matched to a GT (auto + null + manual)

    recall_auto: float            # conservative bound (manual excluded)
    recall_full: float            # optimistic bound (manual counted as matches)
    precision_strict: float
    precision_distinct: float
    f1_strict: float              # f1(recall_full, precision_strict)
    f1_distinct: float            # f1(recall_full, precision_distinct)

    avg_match_cosine: float | None

    redundant_restatements: int
    out_of_scope_fp: int
    dynamic_neutral: int
    possible_gt_gap: int
    hallucinations: int

    source_fidelity: float | None  # verified quotes / AI claims that carry a quote
    manual_band: int


@dataclass
class GroupMetrics:
    """Macro-average over a set of cells (overall / per-question / per-entity / per-tag)."""
    label: str
    n_cells: int
    gt_active: int
    ai_distinct: int
    recall_auto: float
    recall_full: float
    precision_strict: float
    precision_distinct: float
    f1_strict: float
    f1_distinct: float
    avg_match_cosine: float | None
    redundant_restatements: int
    out_of_scope_fp: int
    dynamic_neutral: int
    possible_gt_gap: int
    hallucinations: int


@dataclass
class MetricsReport:
    cells: list[CellMetrics]
    overall: GroupMetrics
    by_question: list[GroupMetrics]
    by_entity: list[GroupMetrics]
    by_tag: list[GroupMetrics] = field(default_factory=list)


@dataclass
class AIOnlyAuditRow:
    """Human-review row for an unmatched AI claim cluster."""
    entity: str
    question: str
    category: str
    value: str
    quote: str
    source_url: str
    verified: bool
    match_type: str
    verification_score: float | None
    semantic_score: float | None
    nearest_gt_claim_id: str
    nearest_gt_claim: str
    nearest_gt_score: float | None
    nearest_gt_method: str
    reason: str


# --- small helpers -----------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).lower()).split())


def _f1(recall: float, precision: float) -> float:
    return 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0


def _safe_div(num: int, den: int) -> float:
    return num / den if den else 0.0


_NULL_LIKE_VALUES = frozenset({
    "",
    "none",
    "null",
    "nan",
    "na",
    "n a",
    "not available",
    "not found",
    "no data",
    "no data found",
    "unknown",
    "undisclosed",
})


def _is_null_like_ai(claim: AIClaim) -> bool:
    """True for explicit no-answer claims, including equivalent not-disclosed text."""
    if claim.is_null:
        return True
    value = _norm(claim.value)
    if value in _NULL_LIKE_VALUES:
        return True
    return "not disclosed" in value or "not reported" in value or "not stated" in value


def _dedup_ai(claims: list[AIClaim]) -> list[list[AIClaim]]:
    """Collapse near-duplicate AI claims into clusters by token_sort_ratio >= AI_DEDUP_RATIO.
    Returns a list of clusters (each a list of the original claims)."""
    clusters: list[list[AIClaim]] = []
    reps: list[str] = []
    for c in claims:
        key = _norm(c.value)
        placed = False
        for i, rep in enumerate(reps):
            if fuzz.token_sort_ratio(key, rep) >= AI_DEDUP_RATIO:
                clusters[i].append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])
            reps.append(key)
    return clusters


def _matches_excluded(value: str, excluded_items) -> str | None:
    """Return the Excluded category if `value` matches an Excluded statement, else None."""
    v = _norm(value)
    if not v:
        return None
    for it in excluded_items:
        if fuzz.token_set_ratio(v, it.excluded_text_norm) >= EXCLUDED_MATCH_RATIO:
            return it.category
    return None


def _matched_ai_claims(aligns) -> list[AIClaim]:
    matched_ai: list[AIClaim] = []
    seen_matched: set[int] = set()
    for a in aligns:
        if a.verdict in _MATCHED_FULL and a.ai_claim is not None and id(a.ai_claim) not in seen_matched:
            seen_matched.add(id(a.ai_claim))
            matched_ai.append(a.ai_claim)
    return matched_ai


def _has_matched_real_answer(aligns) -> bool:
    return any(
        a.verdict in _MATCHED_FULL
        and a.ai_claim is not None
        and not a.gt_claim.is_null
        and not _is_null_like_ai(a.ai_claim)
        for a in aligns
    )


def _best_ai_only_match(cluster: list[AIClaim], ai_only_by_id: dict[int, object]):
    best = None
    for c in cluster:
        ao = ai_only_by_id.get(id(c))
        if ao and ao.best_score is not None:
            if best is None or ao.best_score.combined_score > best[0]:
                best = (ao.best_score.combined_score, ao.best_gt_claim_id, ao.best_score.method)
    return best


def _cluster_reason(
    cluster: list[AIClaim],
    excluded_for_entity: list,
    ai_only_by_id: dict[int, object],
    matched_gt_ids_full: set[str],
    has_matched_real_answer: bool,
) -> tuple[str, str, str, tuple[float, str, str] | None]:
    """Classify one unmatched AI cluster for counting and audit output."""
    excl_cat = None
    for c in cluster:
        excl_cat = _matches_excluded(c.value, excluded_for_entity)
        if excl_cat:
            break
    if excl_cat == "dynamic_counter":
        return (
            "dynamic_neutral",
            "excluded/neutral",
            "matches Excluded category dynamic_counter; removed from precision denominators",
            None,
        )
    if excl_cat == "inclusion_bar":
        return (
            "out_of_scope_fp",
            "excluded/out_of_scope_fp",
            "matches Excluded category inclusion_bar; counted as out-of-scope false positive",
            None,
        )
    if excl_cat == "duplicate":
        return (
            "redundant",
            "redundant",
            "matches Excluded category duplicate; treated as known duplicate of canonical GT",
            None,
        )

    best = _best_ai_only_match(cluster, ai_only_by_id)
    if best and best[0] >= AUTO_MATCH_THRESHOLD and best[1] in matched_gt_ids_full:
        return (
            "redundant",
            "redundant",
            "nearest GT is already matched by another AI claim",
            best,
        )

    if has_matched_real_answer and all(_is_null_like_ai(c) for c in cluster):
        return (
            "dynamic_neutral",
            "excluded/null_neutral",
            "unmatched null-like AI answer ignored because a real answer is already matched in this cell",
            best,
        )

    if any(c.verified for c in cluster):
        return (
            "possible_gt_gap",
            "possible_gt_gap (needs review)",
            "source-verified unmatched AI claim; review for GT gap, granularity split, or near-threshold restatement",
            best,
        )

    return (
        "hallucination",
        "hallucination",
        "unmatched AI claim is not source-verified and has no neutral/redundant signal",
        best,
    )


# --- per-cell computation ----------------------------------------------------
def _cell_metrics(cell: CellAlignment, excluded_for_entity: list) -> CellMetrics:
    aligns = cell.alignments
    gt_active = len(aligns)

    matched_gt_ids_auto = {a.gt_claim.claim_id for a in aligns if a.verdict in _MATCHED_AUTO}
    matched_gt_ids_full = {a.gt_claim.claim_id for a in aligns if a.verdict in _MATCHED_FULL}
    manual_band = sum(1 for a in aligns if a.verdict == "manual")

    recall_auto = _safe_div(len(matched_gt_ids_auto), gt_active)
    recall_full = _safe_div(len(matched_gt_ids_full), gt_active)

    # avg_match_cosine over semantic matches only (exclude null + containment synthetics).
    sem = [a.score.claim_cosine for a in aligns
           if a.verdict in {"auto_match", "manual"} and a.score is not None
           and a.score.method not in {"containment"}]
    avg_cos = round(sum(sem) / len(sem), 4) if sem else None

    # ---- AI claim universe: matched AI claims (deduped across merged groups) + ai_only ----
    matched_ai: list[AIClaim] = []
    seen_matched: set[int] = set()
    for a in aligns:
        if a.verdict in _MATCHED_FULL and a.ai_claim is not None and id(a.ai_claim) not in seen_matched:
            seen_matched.add(id(a.ai_claim))
            matched_ai.append(a.ai_claim)
    ai_only_claims = [ao.ai_claim for ao in cell.ai_only]

    universe = matched_ai + ai_only_claims
    clusters = _dedup_ai(universe)
    ai_raw = len(universe)
    ai_distinct = len(clusters)

    matched_ai_ids = {id(a) for a in matched_ai}
    # Map ai_only AI claim id -> its AIOnly record (for nearest-GT / redundancy test).
    ai_only_by_id = {id(ao.ai_claim): ao for ao in cell.ai_only}
    has_matched_real_answer = _has_matched_real_answer(aligns)

    tp = 0                      # distinct clusters matched to a GT (auto + null + manual)
    redundant = out_of_scope = dynamic_neutral = gap = halluc = 0

    for cluster in clusters:
        if any(id(c) in matched_ai_ids for c in cluster):
            tp += 1
            continue

        # Unmatched cluster — classify by the best-provenance / nearest-GT member.
        metric_bucket, _, _, _ = _cluster_reason(
            cluster,
            excluded_for_entity,
            ai_only_by_id,
            matched_gt_ids_full,
            has_matched_real_answer,
        )
        if metric_bucket == "dynamic_neutral":
            dynamic_neutral += 1
            continue
        if metric_bucket == "out_of_scope_fp":
            out_of_scope += 1
            continue
        if metric_bucket == "redundant":
            redundant += 1
            continue
        if metric_bucket == "possible_gt_gap":
            gap += 1
        else:
            halluc += 1

    # Precision denominators (dynamic_counter neutrals dropped from both;
    # redundant restatements additionally dropped from the distinct denominator).
    den_strict = ai_distinct - dynamic_neutral
    den_distinct = ai_distinct - dynamic_neutral - redundant
    precision_strict = _safe_div(tp, den_strict)
    precision_distinct = _safe_div(tp, den_distinct)

    # source fidelity: distinct AI claims carrying a quote, fraction verified.
    quoted = [cl for cl in clusters if any((c.quote or "").strip() for c in cl)]
    verified_quoted = sum(1 for cl in quoted if any(c.verified for c in cl))
    source_fidelity = round(_safe_div(verified_quoted, len(quoted)), 4) if quoted else None

    return CellMetrics(
        entity=cell.entity, question=cell.gt_question,
        question_type="list" if cell.is_list else "single",
        gt_active=gt_active, ai_raw=ai_raw, ai_distinct=ai_distinct, tp=tp,
        recall_auto=round(recall_auto, 4), recall_full=round(recall_full, 4),
        precision_strict=round(precision_strict, 4), precision_distinct=round(precision_distinct, 4),
        f1_strict=round(_f1(recall_full, precision_strict), 4),
        f1_distinct=round(_f1(recall_full, precision_distinct), 4),
        avg_match_cosine=avg_cos,
        redundant_restatements=redundant, out_of_scope_fp=out_of_scope,
        dynamic_neutral=dynamic_neutral, possible_gt_gap=gap, hallucinations=halluc,
        source_fidelity=source_fidelity, manual_band=manual_band,
    )


def ai_only_audit_rows(result: AlignmentResult, gt: GroundTruth) -> list[AIOnlyAuditRow]:
    """Return unmatched AI claim clusters for human triage; categories are hypotheses."""
    by_cat = gt.excluded_by_category()
    all_excluded = [it for items in by_cat.values() for it in items]
    excl_by_entity: dict[str, list] = {}
    for it in all_excluded:
        excl_by_entity.setdefault(it.entity_norm, []).append(it)

    rows: list[AIOnlyAuditRow] = []
    for cell in result.cells:
        aligns = cell.alignments
        matched_gt_ids_full = {a.gt_claim.claim_id for a in aligns if a.verdict in _MATCHED_FULL}
        matched_ai = _matched_ai_claims(aligns)
        matched_ai_ids = {id(a) for a in matched_ai}
        ai_only_by_id = {id(ao.ai_claim): ao for ao in cell.ai_only}
        has_matched_real_answer = _has_matched_real_answer(aligns)
        gt_by_id: dict[str, GTClaim] = {a.gt_claim.claim_id: a.gt_claim for a in aligns}

        for cluster in _dedup_ai(matched_ai + [ao.ai_claim for ao in cell.ai_only]):
            if any(id(c) in matched_ai_ids for c in cluster):
                continue

            _, category, reason, best = _cluster_reason(
                cluster,
                excl_by_entity.get(cell.entity_norm, []),
                ai_only_by_id,
                matched_gt_ids_full,
                has_matched_real_answer,
            )
            nearest_id = best[1] if best else ""
            nearest = gt_by_id.get(nearest_id)
            nearest_score = best[0] if best else None
            nearest_method = best[2] if best else ""

            for c in cluster:
                rows.append(AIOnlyAuditRow(
                    entity=cell.entity,
                    question=cell.gt_question,
                    category=category,
                    value=str(c.value),
                    quote=c.quote or "",
                    source_url=c.source_url,
                    verified=c.verified,
                    match_type=c.match_type,
                    verification_score=c.verification_score,
                    semantic_score=c.semantic_score,
                    nearest_gt_claim_id=nearest_id or "",
                    nearest_gt_claim=nearest.claim if nearest else "",
                    nearest_gt_score=nearest_score,
                    nearest_gt_method=nearest_method,
                    reason=reason,
                ))

    return rows


# --- aggregation -------------------------------------------------------------
def _macro(label: str, cells: list[CellMetrics]) -> GroupMetrics:
    n = len(cells)
    def mean(attr):
        vals = [getattr(c, attr) for c in cells]
        return round(sum(vals) / n, 4) if n else 0.0
    def mean_opt(attr):
        vals = [getattr(c, attr) for c in cells if getattr(c, attr) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None
    def total(attr):
        return sum(getattr(c, attr) for c in cells)
    return GroupMetrics(
        label=label, n_cells=n,
        gt_active=total("gt_active"), ai_distinct=total("ai_distinct"),
        recall_auto=mean("recall_auto"), recall_full=mean("recall_full"),
        precision_strict=mean("precision_strict"), precision_distinct=mean("precision_distinct"),
        f1_strict=mean("f1_strict"), f1_distinct=mean("f1_distinct"),
        avg_match_cosine=mean_opt("avg_match_cosine"),
        redundant_restatements=total("redundant_restatements"),
        out_of_scope_fp=total("out_of_scope_fp"), dynamic_neutral=total("dynamic_neutral"),
        possible_gt_gap=total("possible_gt_gap"), hallucinations=total("hallucinations"),
    )


def _tag_slices(result: AlignmentResult) -> list[GroupMetrics]:
    """Recall by (type, dimension) tag — uses GT claim tags; tags never enter the math,
    only slice it. Computed as recall_auto over the tagged GT claims."""
    buckets: dict[tuple[str, str], list[bool]] = {}
    for cell in result.cells:
        for a in cell.alignments:
            tag = (a.gt_claim.type or "(untagged)", a.gt_claim.dimension or "(untagged)")
            buckets.setdefault(tag, []).append(a.verdict in _MATCHED_AUTO)
    slices = []
    for (typ, dim), hits in sorted(buckets.items()):
        n = len(hits)
        slices.append(GroupMetrics(
            label=f"type={typ} | dim={dim}", n_cells=n, gt_active=n, ai_distinct=0,
            recall_auto=round(sum(hits) / n, 4), recall_full=round(sum(hits) / n, 4),
            precision_strict=0.0, precision_distinct=0.0,
            f1_strict=0.0, f1_distinct=0.0, avg_match_cosine=None,
            redundant_restatements=0, out_of_scope_fp=0, dynamic_neutral=0,
            possible_gt_gap=0, hallucinations=0,
        ))
    return slices


def compute_metrics(result: AlignmentResult, gt: GroundTruth) -> MetricsReport:
    by_cat = gt.excluded_by_category()
    all_excluded = [it for items in by_cat.values() for it in items]
    excl_by_entity: dict[str, list] = {}
    for it in all_excluded:
        excl_by_entity.setdefault(it.entity_norm, []).append(it)

    cells = [
        _cell_metrics(cell, excl_by_entity.get(cell.entity_norm, []))
        for cell in result.cells
    ]

    questions = sorted({c.question for c in cells})
    entities = sorted({c.entity for c in cells})
    return MetricsReport(
        cells=cells,
        overall=_macro("ALL", cells),
        by_question=[_macro(q, [c for c in cells if c.question == q]) for q in questions],
        by_entity=[_macro(e, [c for c in cells if c.entity == e]) for e in entities],
        by_tag=_tag_slices(result),
    )


# --- self-check / summary printer --------------------------------------------
def _fmt(g: GroupMetrics) -> str:
    cos = f"{g.avg_match_cosine:.2f}" if g.avg_match_cosine is not None else "  - "
    return (f"R_auto={g.recall_auto:.2f} R_full={g.recall_full:.2f} | "
            f"P_strict={g.precision_strict:.2f} P_dist={g.precision_distinct:.2f} | "
            f"F1={g.f1_distinct:.2f} | cos={cos} | "
            f"possible_gap={g.possible_gt_gap} halluc={g.hallucinations} "
            f"redund={g.redundant_restatements}")


def _selfcheck(gt_path: str, pipe_path: str) -> None:
    gt = read_ground_truth(gt_path)
    pipe = read_pipeline_output(pipe_path)
    result = align(gt, pipe)
    report = compute_metrics(result, gt)
    audit_rows = ai_only_audit_rows(result, gt)

    print(f"\n=== metrics report ===")
    print(f"  GT       : {gt_path}")
    print(f"  pipeline : {pipe_path}\n")

    print("--- by question (official; do not merge across question types) ---")
    for g in report.by_question:
        print(f"  {g.label:16} ({g.n_cells:2} cells, {g.gt_active:3} GT): {_fmt(g)}")

    print("\n  Cross-question aggregate omitted from CLI output: Sustainability, MilkTypes, "
          "and ParentCompany are reported separately so short-answer scores do not mask "
          "the Sustainability extraction result.")
    print("  Precision is reported BOTH ways: strict penalises redundancy, distinct drops "
          "restatements of already-matched facts.")

    print("\n--- tag slice (recall_auto by type/dimension) ---")
    for g in report.by_tag:
        if "(untagged)" in g.label:
            continue
        print(f"  {g.label:42} n={g.gt_active:3} recall_auto={g.recall_auto:.2f}")

    print("\n--- AI-only audit (hypotheses for human review) ---")
    print("  possible_gt_gap (needs review) means the AI quote is source-verified, "
          "not that GT is definitely missing.")
    current = None
    for row in sorted(audit_rows, key=lambda r: (r.question, r.entity, r.category, r.value)):
        group_key = (row.entity, row.question)
        if group_key != current:
            current = group_key
            print(f"\n  [{row.entity} / {row.question}]")
        score = f"{row.nearest_gt_score:.2f}" if row.nearest_gt_score is not None else "n/a"
        ver_score = "" if row.verification_score is None else row.verification_score
        print(f"    - {row.category}: {row.value[:140]!r}")
        print(f"      verified={row.verified} match={row.match_type} ver_score={ver_score} | "
              f"nearest_gt={row.nearest_gt_claim_id or '(none)'} score={score} "
              f"method={row.nearest_gt_method or 'n/a'}")
        print(f"      source={row.source_url}")
        if row.quote:
            print(f"      quote={row.quote[:180]!r}")
        print(f"      reason={row.reason}")

    official_ai_only = {
        "possible_gt_gap (needs review)": sum(c.possible_gt_gap for c in report.cells),
        "hallucination": sum(c.hallucinations for c in report.cells),
        "redundant": sum(c.redundant_restatements for c in report.cells),
        "excluded/out_of_scope_fp": sum(c.out_of_scope_fp for c in report.cells),
        "excluded/neutral": sum(c.dynamic_neutral for c in report.cells),
    }
    displayed_rows = Counter(row.category for row in audit_rows)
    print("\n  AI-ONLY TRIAGE SUMMARY")
    print("  Official metric counts are deduped AI-only clusters; displayed audit rows are "
          "raw claim/provenance rows and may be larger.")
    print("  Official clusters used in metrics:")
    for label, count in official_ai_only.items():
        print(f"    - {label}: {count}")
    print("  Displayed audit rows:")
    for label, count in sorted(displayed_rows.items()):
        print(f"    - {label}: {count}")
    print("  excluded/neutral rows are not counted as hallucinations; "
          "excluded/out_of_scope_fp rows remain false positives.")
    print("  NOTE: sub-page localisation (char_span) omitted - absent from Excel Provenance.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python src/eval/metrics.py <ground_truth.xlsx> <pipeline_output.xlsx>")
        sys.exit(2)
    _selfcheck(sys.argv[1], sys.argv[2])
