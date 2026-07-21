"""
Aligner for the extraction evaluation framework (Stage 10 / RQ3).

Aligns pipeline-extracted (AI) claims to analyst ground-truth (GT) claims, per
(entity, question) cell. This module computes the match signals and assigns
matches; it does NOT compute recall/precision/F1 (that is metrics.py, built next).

Signals (per GT-claim x AI-claim pair):
  - claim_cosine  : cosine(embed(gt.claim), embed(ai.value)) via src.embed.embed_batch.
                    For SHORT GT claims (< MIN_TOKENS_FOR_COSINE tokens, e.g. milk
                    types, parent-company names) embedding a 1-2 word string is
                    unreliable, so Signal A falls back to rapidfuzz token_sort_ratio.
  - quote_overlap : token-set F1 (Dice) between gt.verbatim_quote and ai.quote.
                    0.0 when either quote is absent (cosine-only fallback).
  - combined_score = CLAIM_COSINE_WEIGHT * claim_cosine
                     + QUOTE_OVERLAP_WEIGHT * quote_overlap        (weights sum to 1)

Matching: greedy 1:1 assignment of GT *groups* to AI claims, highest score first.
  - A GT "group" is normally a single GT claim. The ONLY one-AI-to-many-GT case is
    when GT rows share a quote_id (the analyst's merged-claim marker): such rows form
    one group, and a single AI claim matching the group credits all its members.
    This is the deliberate guard against recall inflation (a vague AI claim cannot
    freely credit several unrelated GT rows).
  - Only pairs scoring >= AUTO_MISS_THRESHOLD are assignable; weaker pairs are not
    matches. An AI claim wins at most one group; a group wins at most one AI claim.

Bands (config constants, provisional — calibrated empirically, NOT principled):
  combined_score >= AUTO_MATCH_THRESHOLD            -> auto_match
  AUTO_MISS_THRESHOLD <= score < AUTO_MATCH_THRESHOLD -> manual   (human decides)
  score < AUTO_MISS_THRESHOLD                        -> auto_miss

Null handling (structural, bypasses the score bands):
  GT-null ("None (not disclosed…)") matched by an AI-null  -> null_match (correct).
  GT-null with no AI-null                                  -> auto_miss.
  AI-null with no GT-null                                  -> ai_only (precision-side).

NOT done here (deferred to metrics.py): AI-side near-duplicate collapse for the
precision denominator, the precision/recall/F1 math, and the hallucination vs
GT-gap classification of ai_only claims.

Run directly to review alignment on real data:
    python src/eval/aligner.py <ground_truth.xlsx> <pipeline_output.xlsx>
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

# --- repo-root bootstrap -----------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rapidfuzz import fuzz

from src.embed import embed_batch
from src.verify import _cosine

from src.eval.gt_reader import (
    GT_SHEET_TO_QUESTION,
    GTClaim,
    GroundTruth,
    read_ground_truth,
)
from src.eval.pipeline_reader import (
    AIClaim,
    PipelineOutput,
    read_pipeline_output,
)


# =====================================================================
# Config constants (provisional — tune against the smoke test + real run).
# Kept here so the whole eval framework's knobs live in one obvious place.
# =====================================================================
CLAIM_COSINE_WEIGHT = 0.65
QUOTE_OVERLAP_WEIGHT = 0.35

AUTO_MATCH_THRESHOLD = 0.82   # >= this -> auto_match
AUTO_MISS_THRESHOLD = 0.60    # < this  -> auto_miss; [this, AUTO_MATCH) -> manual

MIN_TOKENS_FOR_COSINE = 10    # GT claims shorter than this use token_sort_ratio for Signal A

# Reverse of GT_SHEET_TO_QUESTION, to fold AI claims (keyed by pipeline question
# name) back onto the GT canonical question key.
QUESTION_TO_GT_SHEET = {v: k for k, v in GT_SHEET_TO_QUESTION.items()}

# Single-answer name containment (Parent company). Strip ONLY unambiguous legal
# forms — deliberately NOT geographic qualifiers ("usa", "north america") nor
# "group"/"holdings"/"the", which can distinguish genuinely different entities
# (e.g. "Hain Celestial Group"). Token-subset containment then matches a name to a
# more/less specific form of itself ("Danone North America" vs "Danone") while
# never collapsing different regions ("Danone USA" vs "Danone Canada").
_LEGAL_PHRASES = ("public benefit corporation",)        # multi-word, removed first
_LEGAL_TOKENS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "llc", "ltd", "limited", "plc", "pbc",
})


# --- result model ------------------------------------------------------------
@dataclass
class PairScore:
    claim_cosine: float          # Signal A (cosine, or token_sort_ratio/100 for short claims)
    quote_overlap: float         # Signal B (token-set F1)
    combined_score: float        # weighted sum, clamped to [0, 1]
    method: str                  # cosine | fuzz | fuzz(fallback) | *+noquote | containment


@dataclass
class GTAlignment:
    """One GT claim and its assigned AI claim (or none)."""
    gt_claim: GTClaim
    verdict: str                          # auto_match | manual | auto_miss | null_match | no_ai_data
    ai_claim: AIClaim | None = None       # assigned AI claim
    score: PairScore | None = None        # score vs the assigned AI claim
    via_quote_id: str | None = None       # quote_id when credited as part of a merged group
    group_representative_id: str | None = None
    note: str = ""


@dataclass
class AIOnly:
    """An AI claim not assigned to any GT claim — a precision-side leftover."""
    ai_claim: AIClaim
    best_gt_claim_id: str | None = None   # nearest GT (diagnostic only)
    best_score: PairScore | None = None
    note: str = ""


@dataclass
class CellAlignment:
    entity: str
    entity_norm: str
    gt_question: str                      # canonical key
    pipeline_question: str                # mapped pipeline name
    is_list: bool
    alignments: list[GTAlignment] = field(default_factory=list)
    ai_only: list[AIOnly] = field(default_factory=list)


@dataclass
class AlignmentResult:
    cells: list[CellAlignment]
    weights: tuple[float, float]
    thresholds: tuple[float, float]
    unmapped_questions: list[str] = field(default_factory=list)


# --- signal helpers ----------------------------------------------------------
def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _is_short(claim: str) -> bool:
    return len(claim.split()) < MIN_TOKENS_FOR_COSINE


def _token_f1(a: str, b: str) -> float:
    """Token-set F1 (Dice) between two quote strings. 0.0 if either is empty."""
    ta = {t for t in a.lower().split() if t}
    tb = {t for t in b.lower().split() if t}
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter == 0:
        return 0.0
    return 2.0 * inter / (len(ta) + len(tb))


def _band(score: float) -> str:
    if score >= AUTO_MATCH_THRESHOLD:
        return "auto_match"
    if score >= AUTO_MISS_THRESHOLD:
        return "manual"
    return "auto_miss"


def _pair_score(gt: GTClaim, ai: AIClaim, emb: dict[str, list[float]]) -> PairScore:
    """Compute (claim_cosine, quote_overlap, combined_score) for one GT/AI pair."""
    if _is_short(gt.claim):
        sig_a = fuzz.token_sort_ratio(gt.claim.lower(), str(ai.value).lower()) / 100.0
        method = "fuzz"
    else:
        va, vb = emb.get(gt.claim), emb.get(str(ai.value))
        if va is not None and vb is not None:
            sig_a = _clamp01(_cosine(va, vb))
            method = "cosine"
        else:
            sig_a = fuzz.token_sort_ratio(gt.claim.lower(), str(ai.value).lower()) / 100.0
            method = "fuzz(fallback)"

    overlap = _token_f1(gt.verbatim_quote, ai.quote)
    # Edge 7.6: when a quote is ABSENT on either side, overlap can't corroborate, so
    # the combined score falls back to the claim signal alone (no 0.35 dead-weight).
    # When both quotes are present but share no tokens, we keep the weighted blend —
    # a high cosine with zero overlap should be penalised toward manual review.
    quote_available = bool(gt.verbatim_quote.strip()) and bool(ai.quote.strip())
    if quote_available:
        combined = CLAIM_COSINE_WEIGHT * sig_a + QUOTE_OVERLAP_WEIGHT * overlap
    else:
        combined = sig_a
        method += "+noquote"
    return PairScore(claim_cosine=round(sig_a, 4), quote_overlap=round(overlap, 4),
                     combined_score=round(_clamp01(combined), 4), method=method)


# --- Problem 1: list-of-short-items splitting (e.g. MilkTypes) ----------------
def _is_short_list_cell(gt_real: list[GTClaim]) -> bool:
    """Milk-types-style cell: a list question whose GT items are predominantly
    short (single words). Distinguishes MilkTypes from Sustainability, whose GT
    items are full sentences and stay on the cosine path untouched."""
    if not gt_real:
        return False
    short = sum(1 for g in gt_real if _is_short(g.claim))
    return short > len(gt_real) / 2


def _split_short_list_ai(ai_real: list[AIClaim]) -> list[AIClaim]:
    """Split comma-joined AI values into individual items, each a synthetic AIClaim
    inheriting its parent's provenance. Deduplicate items across all the cell's AI
    claims by normalised string, keeping the best provenance (verified, then
    exact > fuzzy > none) so repeated lists don't inflate the precision denominator."""
    rank = {"exact": 2, "fuzzy": 1, "none": 0}
    best: dict[str, AIClaim] = {}
    for a in ai_real:
        for raw in str(a.value).split(","):
            item = raw.strip()
            if not item:
                continue
            key = item.lower()
            syn = AIClaim(
                entity=a.entity, entity_norm=a.entity_norm, question=a.question,
                value=item, quote=a.quote, source_url=a.source_url,
                verified=a.verified, match_type=a.match_type,
                verification_score=a.verification_score, semantic_score=a.semantic_score,
                confidence_score=a.confidence_score, source_depth=a.source_depth,
                is_null=False, char_span=a.char_span,
            )
            cur = best.get(key)
            if cur is None or (syn.verified, rank.get(syn.match_type, 0)) > (
                    cur.verified, rank.get(cur.match_type, 0)):
                best[key] = syn
    return list(best.values())


# --- Problem 2: single-answer name containment (e.g. Parent company) ----------
def _core_tokens(value: str) -> set[str]:
    """Significant name tokens: lowercased, punctuation-stripped, legal forms removed."""
    s = re.sub(r"[^\w\s]", " ", str(value).lower())
    for phrase in _LEGAL_PHRASES:
        s = s.replace(phrase, " ")
    return {t for t in s.split() if len(t) > 1 and t not in _LEGAL_TOKENS}


def _name_contained(a: str, b: str) -> bool:
    """True when one name's core tokens are a full subset of the other's — a name
    matched to a more/less specific form of itself, without collapsing different
    regions (different geographic tokens break the subset relation)."""
    ta, tb = _core_tokens(a), _core_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


# --- cell assembly -----------------------------------------------------------
def _assemble_cells(
    gt: GroundTruth,
    pipe: PipelineOutput,
) -> tuple[dict[tuple[str, str], dict], list[str]]:
    """Build (entity_norm, canonical_question) -> {label, gt_claims, ai_claims}.

    Union of cells present in GT and in the pipeline output (so AI-only claims in
    GT-empty cells are still captured). Returns also the list of unmapped pipeline
    question names that were skipped.
    """
    cells: dict[tuple[str, str], dict] = {}

    def _slot(entity_norm: str, q: str, label: str) -> dict:
        key = (entity_norm, q)
        if key not in cells:
            cells[key] = {"label": label, "gt": [], "ai": []}
        elif not cells[key]["label"]:
            cells[key]["label"] = label
        return cells[key]

    for c in gt.active_claims:
        _slot(c.entity_norm, c.question, c.entity)["gt"].append(c)

    unmapped: set[str] = set()
    for a in pipe.ai_claims:
        canonical = QUESTION_TO_GT_SHEET.get(a.question)
        if canonical is None:
            unmapped.add(a.question)
            continue
        _slot(a.entity_norm, canonical, a.entity)["ai"].append(a)

    return cells, sorted(unmapped)


def _gather_embeddings(cells: dict[tuple[str, str], dict]) -> dict[str, list[float]]:
    """Embed only what the cosine path needs: long GT claim texts, plus AI values
    in any cell that has at least one long GT claim. One batched call."""
    need: set[str] = set()
    for slot in cells.values():
        long_gts = [g for g in slot["gt"] if not g.is_null and not _is_short(g.claim)]
        if not long_gts:
            continue
        need.update(g.claim for g in long_gts)
        need.update(str(a.value) for a in slot["ai"] if not a.is_null)

    texts = [t for t in need if t.strip()]
    if not texts:
        return {}
    try:
        vectors = embed_batch(texts)
    except Exception as exc:  # edge 7.4 — fail loud, never silently score 0
        raise RuntimeError(
            "Embedding service unavailable - cannot compute claim_cosine. Ensure "
            f"Ollama is running on the configured host before re-running. ({exc})"
        ) from exc
    return dict(zip(texts, vectors))


# --- core matching -----------------------------------------------------------
def _match_cell(
    slot: dict,
    emb: dict[str, list[float]],
    is_list: bool,
) -> tuple[list[GTAlignment], list[AIOnly]]:
    gts: list[GTClaim] = slot["gt"]
    ais: list[AIClaim] = slot["ai"]

    gt_real = [g for g in gts if not g.is_null]
    gt_null = [g for g in gts if g.is_null]
    ai_real = [a for a in ais if not a.is_null]
    ai_null = [a for a in ais if a.is_null]

    no_ai_at_all = len(ais) == 0
    alignments: list[GTAlignment] = []
    used_ai_real: set[int] = set()

    # Problem 1: in milk-types-style cells, split comma-joined AI values into items.
    if is_list and _is_short_list_cell(gt_real):
        ai_real = _split_short_list_ai(ai_real)

    # ---- real-claim greedy group matching ----
    # Pairwise scores S[i][j] for gt_real[i] x ai_real[j]. Problem 2: for
    # single-answer cells, a token-subset name containment is an automatic match.
    def _score(g: GTClaim, a: AIClaim) -> PairScore:
        if not is_list and _name_contained(g.claim, str(a.value)):
            return PairScore(claim_cosine=1.0,
                             quote_overlap=round(_token_f1(g.verbatim_quote, a.quote), 4),
                             combined_score=1.0, method="containment")
        return _pair_score(g, a, emb)

    S = [[_score(g, a) for a in ai_real] for g in gt_real]

    # GT groups: claims sharing a non-empty quote_id merge; others are singletons.
    groups: list[list[int]] = []
    by_qid: dict[str, list[int]] = {}
    for i, g in enumerate(gt_real):
        if g.quote_id:
            by_qid.setdefault(g.quote_id, []).append(i)
        else:
            groups.append([i])
    groups.extend(by_qid.values())

    # Candidate (score, group_idx, ai_idx), greedily assigned best-first, but only
    # pairs at/above the auto-miss floor are real matches.
    candidates = []
    for gi, members in enumerate(groups):
        for j in range(len(ai_real)):
            gscore = max(S[i][j].combined_score for i in members)
            if gscore >= AUTO_MISS_THRESHOLD:
                candidates.append((gscore, gi, j))
    candidates.sort(key=lambda t: t[0], reverse=True)

    group_to_ai: dict[int, int] = {}
    used_group: set[int] = set()
    for gscore, gi, j in candidates:
        if gi in used_group or j in used_ai_real:
            continue
        group_to_ai[gi] = j
        used_group.add(gi)
        used_ai_real.add(j)

    for gi, members in enumerate(groups):
        is_merged = len(members) > 1
        if gi in group_to_ai:
            j = group_to_ai[gi]
            ai = ai_real[j]
            rep_i = max(members, key=lambda i: S[i][j].combined_score)
            rep_score = S[rep_i][j].combined_score
            for i in members:
                own_score = S[i][j].combined_score
                # Each member's verdict derives from its OWN score only — same
                # logic as non-group claims. The group shares the AI claim, not
                # the score. A score below AUTO_MISS_THRESHOLD is a transparent,
                # evidence-based miss, not a silent promotion.
                member_verdict = _band(own_score)
                if is_merged:
                    base_note = (f"merged group via quote_id={gt_real[i].quote_id} "
                                 f"(representative {gt_real[rep_i].claim_id}, score {rep_score})")
                    if member_verdict == "manual" and i != rep_i:
                        member_note = (base_note + f"; passenger own score {own_score} "
                                       f"below auto_match threshold")
                    else:
                        member_note = base_note
                else:
                    member_note = ""
                alignments.append(GTAlignment(
                    gt_claim=gt_real[i],
                    verdict=member_verdict,
                    ai_claim=ai,
                    score=S[i][j],
                    via_quote_id=gt_real[i].quote_id if is_merged else None,
                    group_representative_id=(gt_real[rep_i].claim_id if is_merged else None),
                    note=member_note,
                ))
        else:
            # Unmatched group. Show the strongest candidate as a diagnostic.
            for i in members:
                best_note = ""
                if ai_real:
                    bj = max(range(len(ai_real)), key=lambda j: S[i][j].combined_score)
                    bs = S[i][bj]
                    consumed = bj in used_ai_real
                    best_note = (f"best candidate {str(ai_real[bj].value)[:40]!r} "
                                 f"score {bs.combined_score}"
                                 + (" (consumed by another GT)" if consumed else ""))
                alignments.append(GTAlignment(
                    gt_claim=gt_real[i],
                    verdict="no_ai_data" if no_ai_at_all else "auto_miss",
                    note=best_note,
                ))

    # ---- null structural matching ----
    remaining_null = list(range(len(ai_null)))
    used_ai_null: set[int] = set()
    for g in gt_null:
        if remaining_null:
            j = remaining_null.pop(0)
            used_ai_null.add(j)
            alignments.append(GTAlignment(
                gt_claim=g, verdict="null_match", ai_claim=ai_null[j],
                note="structural null match (GT-null matched by AI-null)",
            ))
        else:
            alignments.append(GTAlignment(
                gt_claim=g,
                verdict="no_ai_data" if no_ai_at_all else "auto_miss",
                note="GT-null; AI returned no null sentinel here",
            ))

    # ---- AI-only leftovers (precision-side; classification deferred to metrics) ----
    ai_only: list[AIOnly] = []
    for j, a in enumerate(ai_real):
        if j in used_ai_real:
            continue
        best_id, best_score = None, None
        if gt_real:
            bi = max(range(len(gt_real)), key=lambda i: S[i][j].combined_score)
            best_id, best_score = gt_real[bi].claim_id, S[bi][j]
        ai_only.append(AIOnly(ai_claim=a, best_gt_claim_id=best_id, best_score=best_score))
    for j, a in enumerate(ai_null):
        if j in used_ai_null:
            continue
        ai_only.append(AIOnly(ai_claim=a, note="AI-null with no GT-null in this cell"))

    return alignments, ai_only


def align(gt: GroundTruth, pipe: PipelineOutput) -> AlignmentResult:
    """Align AI claims to GT claims across all cells. No metrics computed here."""
    cells, unmapped = _assemble_cells(gt, pipe)
    emb = _gather_embeddings(cells)

    results: list[CellAlignment] = []
    for (entity_norm, q), slot in cells.items():
        is_list = any(g.is_list for g in slot["gt"]) or (q in {"Sustainability", "MilkTypes"})
        alignments, ai_only = _match_cell(slot, emb, is_list)
        results.append(CellAlignment(
            entity=slot["label"],
            entity_norm=entity_norm,
            gt_question=q,
            pipeline_question=GT_SHEET_TO_QUESTION.get(q, q),
            is_list=is_list,
            alignments=alignments,
            ai_only=ai_only,
        ))

    results.sort(key=lambda c: (c.entity_norm, c.gt_question))
    return AlignmentResult(
        cells=results,
        weights=(CLAIM_COSINE_WEIGHT, QUOTE_OVERLAP_WEIGHT),
        thresholds=(AUTO_MATCH_THRESHOLD, AUTO_MISS_THRESHOLD),
        unmapped_questions=unmapped,
    )


# --- self-check / review printer ---------------------------------------------
def _trunc(text, n: int = 46) -> str:
    s = str(text)
    return s if len(s) <= n else s[: n - 3] + "..."


def _selfcheck(gt_path: str, pipe_path: str) -> None:
    gt = read_ground_truth(gt_path)
    pipe = read_pipeline_output(pipe_path)
    result = align(gt, pipe)

    print(f"\n=== aligner review ===")
    print(f"  GT       : {gt_path}")
    print(f"  pipeline : {pipe_path}")
    print(f"  weights  : claim_cosine={result.weights[0]}, quote_overlap={result.weights[1]}")
    print(f"  bands    : auto_match>={result.thresholds[0]}, auto_miss<{result.thresholds[1]} "
          f"(between = manual)")
    if result.unmapped_questions:
        print(f"  ! unmapped pipeline questions (skipped): {result.unmapped_questions}")

    band_counts: dict[str, int] = {}
    ai_only_total = 0
    for cell in result.cells:
        print(f"\n--- {cell.entity} / {cell.gt_question} "
              f"({'list' if cell.is_list else 'single'}) ---")
        for a in cell.alignments:
            band_counts[a.verdict] = band_counts.get(a.verdict, 0) + 1
            sc = a.score
            score_str = (f"A={sc.claim_cosine:.2f} Q={sc.quote_overlap:.2f} "
                         f"C={sc.combined_score:.2f} [{sc.method}]" if sc else "n/a")
            ai_str = _trunc(a.ai_claim.value) if a.ai_claim else "(none)"
            print(f"  [{a.verdict:10}] GT {_trunc(a.gt_claim.claim)!r}")
            print(f"               -> AI {ai_str!r}  {score_str}")
            if a.note:
                print(f"                  - {a.note}")
        for ao in cell.ai_only:
            ai_only_total += 1
            bs = ao.best_score
            extra = (f" | nearest GT {ao.best_gt_claim_id} C={bs.combined_score:.2f}"
                     if bs else "")
            verified = f" verified={ao.ai_claim.verified} match={ao.ai_claim.match_type}"
            print(f"  [ai_only   ] AI {_trunc(ao.ai_claim.value)!r}{verified}{extra}")
            if ao.note:
                print(f"                  - {ao.note}")

    print("\n--- verdict band summary ---")
    for v in ("auto_match", "manual", "auto_miss", "null_match", "no_ai_data"):
        print(f"  {v:11}: {band_counts.get(v, 0)}")
    print(f"  ai_only    : {ai_only_total}  (hallucination vs GT-gap split deferred to metrics.py)")
    total = sum(band_counts.values())
    if total:
        man = band_counts.get("manual", 0)
        print(f"\n  manual-review band = {man}/{total} = {100*man/total:.1f}% of GT rows "
              f"(target < ~25%; revisit thresholds if larger)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python src/eval/aligner.py "
              "<ground_truth.xlsx> <pipeline_output.xlsx>")
        sys.exit(2)
    _selfcheck(sys.argv[1], sys.argv[2])
