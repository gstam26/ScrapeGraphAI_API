"""
Generic evaluation framework — works for any pipeline task.

Reads a flat ground-truth workbook and a pipeline output workbook, aligns them
per (entity, question) cell, and reports precision, recall, F1, and hallucination
rate.  Does NOT require Ollama to be running: matching is pure rapidfuzz + token F1.

---------------------------------------------------------------------------
GT workbook format  (sheet "GroundTruth"):

  entity          | question         | value          | is_list | verbatim_quote | source_url | notes
  Wikimedia Found.| Year founded     | 2003           | False   | Since our...   | https://...| ""
  Wikimedia Found.| Main projects    | Wikipedia      | True    |                |            |
  Wikimedia Found.| Main projects    | Wikidata       | True    |                |            |

  - One row per expected claim value.
  - is_list=True: multiple rows per (entity, question) — set-based recall.
  - is_list=False: one row per (entity, question) — single-answer match.
  - verbatim_quote: optional; boosts match confidence when present.
  - "None (not disclosed)": use this exact string when the GT analyst confirmed the
    information is absent from the website.  A pipeline null-sentinel matching it
    counts as a correct true negative; a pipeline number/claim scores as hallucination.

Pipeline output read from the Provenance sheet:
  Entity | Column | Claim | Quote | Verified | Match Type | Semantic Score | Source URL

"Column" must match GT "question" (case-insensitive after normalisation).
"Entity" must match GT "entity" (case-insensitive after normalisation).

---------------------------------------------------------------------------
Match signals:
  value_score = token_sort_ratio(gt.value, ai.value) / 100
  quote_score = token-set F1(gt.verbatim_quote, ai.quote)   (0.0 if either absent)
  combined    = CLAIM_W * value_score + QUOTE_W * quote_score  (if both quotes present)
              = value_score                                     (quote absent on either side)

  MATCH_THRESHOLD  = 0.65   → auto-match  (counted as TP)
  REVIEW_THRESHOLD = 0.45   → review band (counted as TP for F1; flag for manual inspection)
  Below REVIEW_THRESHOLD    → auto-miss   (FN for GT, FP for AI)

  For "None (not disclosed)" GT values: matched only by AI claims that also contain
  "none" or "not disclosed" after normalisation.  Any other AI claim in the same cell
  is a hallucination regardless of score.

---------------------------------------------------------------------------
Precision / recall / F1 definitions:
  TP  = GT claims matched by at least one AI claim (auto-match or review)
  FN  = GT claims with no AI match  (recall failures)
  FP  = AI claims not matched to any GT claim  (hallucination / extra)

  recall    = TP / (TP + FN)
  precision = TP / (TP + FP)      [clamped to 1.0 if TP+FP=0]
  F1        = 2 * P * R / (P + R) [0 if both 0]
  hallucination_rate = FP / max(1, TP + FP)

  For single-answer questions (is_list=False):
    After value-level dedup of AI claims, the same match/miss rules apply.

---------------------------------------------------------------------------
Usage:
  python src/eval/generic_eval.py <ground_truth.xlsx> <pipeline_output.xlsx>
  python src/eval/generic_eval.py <gt.xlsx> <pipe.xlsx> --output report.xlsx
  python src/eval/generic_eval.py <gt.xlsx> <pipe.xlsx> --verbose
  python src/eval/generic_eval.py <gt.xlsx> <pipe.xlsx> --sheet matrix
      (score the deliverable Matrix sheet instead of Provenance — measures
       what the output table SHOWS after aggregation and display capping)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CLAIM_W = 0.65
QUOTE_W = 0.35
MATCH_THRESHOLD  = 0.65   # >= this -> auto_match
REVIEW_THRESHOLD = 0.45   # >= this -> review (still counted as TP for F1)

# Fuzzy near-duplicate collapse of AI claims before scoring precision — the
# same constant/idea as the plant-milk evaluator (eval_lib/metrics.py):
# "Geneva, Switzerland" / "Geneva" / "based in Geneva" are ONE claim, not
# three, so redundant phrasings don't each count as a hallucination.
AI_DEDUP_RATIO = 95

# Semantic value matching. Pure lexical overlap (token_sort_ratio) scores a
# correct paraphrase — "Enable universal access to knowledge" vs "empower
# people worldwide to collect, develop and share knowledge" — as BOTH a
# recall miss AND a hallucination (observed on task1 Wikimedia, 2026-07-15).
# We add embedding cosine (nomic-embed, the pipeline's own embedder) as a
# second value signal and take the MAX of lexical and semantic, so a
# lexically-distant but meaning-equivalent pair is rescued. Cosine and
# token_sort_ratio/100 share the 0..1 scale, so the existing thresholds
# apply unchanged. A pair whose lexical score is below REVIEW but whose
# semantic score clears it is a "semantic rescue" — counted and reported so
# the change is auditable, never silent. Degrades gracefully: if Ollama is
# unreachable the evaluator falls back to lexical-only with a warning.
SEMANTIC_MIN = 0.60   # cosine floor for a semantic rescue to be believed

_NULL_SENTINEL = "none (not disclosed)"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class GTRow:
    entity: str
    entity_norm: str
    question: str
    question_norm: str
    value: str
    is_list: bool
    verbatim_quote: str
    source_url: str
    notes: str
    is_null: bool


@dataclass
class AIRow:
    entity: str
    entity_norm: str
    question: str
    question_norm: str
    value: str
    quote: str
    verified: bool
    match_type: str
    source_url: str


@dataclass
class PairResult:
    gt_value: str
    ai_value: Optional[str]
    value_score: float
    quote_score: float
    combined: float
    verdict: str   # auto_match | review | semantic_review | auto_miss | null_match | no_ai_data
    semantic: float = 0.0


@dataclass
class CellResult:
    entity: str
    question: str
    is_list: bool
    gt_pairs: list[PairResult]
    ai_only: list[AIRow]              # AI claims not matched to any GT (FP)
    redundant: list[AIRow] = field(default_factory=list)  # restatements of a credited claim (not FP)


@dataclass
class EvalResult:
    cells: list[CellResult]
    per_question: dict[str, dict]   # question -> {P, R, F1, hallucination_rate, ...}
    overall: dict


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(str(text).strip().lower().split())


def _is_null(value: str) -> bool:
    return _norm(value) == _NULL_SENTINEL or "not disclosed" in _norm(value)


def _token_f1(a: str, b: str) -> float:
    ta = {t for t in a.lower().split() if t}
    tb = {t for t in b.lower().split() if t}
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return 0.0 if inter == 0 else 2 * inter / (len(ta) + len(tb))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def embed_values(texts: list[str]) -> Optional[dict[str, list[float]]]:
    """Batch-embed distinct texts with the pipeline's nomic-embed, mean-centred.

    Returns {text: centred_vector} or None if embeddings are unavailable
    (Ollama down / not installed) — the caller then falls back to lexical-only.
    Mean-centring removes the large shared component that makes every raw
    nomic cosine sit in a narrow high band (the same anisotropy fix group.py
    applies before clustering); without it semantic "similarity" is
    uninformative and would over-match.
    """
    uniq = sorted({t for t in texts if t.strip()})
    if len(uniq) < 3:
        return None  # too few to estimate a meaningful mean
    try:
        from config import OLLAMA_DOC_PREFIX
        from src.embed import embed_batch
        vecs = embed_batch([OLLAMA_DOC_PREFIX + t for t in uniq])
    except Exception as e:  # noqa: BLE001 — graceful lexical fallback
        print(f"  [semantic matching OFF] embeddings unavailable ({type(e).__name__}: {e})")
        print("  -> falling back to lexical matching only.")
        return None
    dim = len(vecs[0])
    n = len(vecs)
    mean = [sum(v[i] for v in vecs) / n for i in range(dim)]
    return {t: [x - m for x, m in zip(v, mean)] for t, v in zip(uniq, vecs)}


class _CosineScorer:
    """Semantic scorer over pre-embedded, mean-centred value vectors.

    The abstraction (score / min_score / name) is shared with the
    experimental cross-encoder backend (src/eval/cross_encoder.py) so the
    alignment logic never knows which one is running."""

    name = "nomic-embed mean-centred cosine"
    min_score = SEMANTIC_MIN

    def __init__(self, vectors: dict[str, list[float]]):
        self._v = vectors

    def score(self, a: str, b: str) -> float:
        if a in self._v and b in self._v:
            return _cosine(self._v[a], self._v[b])
        return 0.0


def _build_semantic(texts: list[str], backend: str):
    """Return a semantic scorer or None (=> lexical-only), always printing
    which one is active — matching behaviour must never be ambiguous."""
    if backend == "cross-encoder":
        try:
            from src.eval.cross_encoder import CrossEncoderScorer
            scorer = CrossEncoderScorer()
            print(f"  [semantic matching ON] {scorer.name}")
            print("  -> threshold NOT yet validated against human labels; "
                  "treat semantic_review verdicts as flags, not credits.")
            return scorer
        except Exception as e:  # noqa: BLE001 — graceful lexical fallback
            print(f"  [semantic matching OFF] cross-encoder unavailable "
                  f"({type(e).__name__}: {e})")
            print("  -> falling back to lexical matching only.")
            return None
    vectors = embed_values(texts)
    if vectors is None:
        return None
    print(f"  [semantic matching ON] embedded {len(vectors)} distinct values "
          f"(mean-centred nomic-embed)")
    return _CosineScorer(vectors)


def _pair_score(
    gt: GTRow, ai: AIRow, sem=None,
) -> tuple[float, float, float, float]:
    """Return (value_score, quote_score, combined_lexical, semantic).

    value_score is the MAX of lexical token_sort_ratio and semantic score
    (for display); combined_lexical is the lexical-only score the confident
    auto_match / review bands are judged on; the semantic score rescues
    otherwise missed pairs into the flagged semantic_review band (_verdict)."""
    vs_lex = fuzz.token_sort_ratio(_norm(gt.value), _norm(ai.value)) / 100.0
    qs = 0.0
    quote_available = bool(gt.verbatim_quote.strip()) and bool(ai.quote.strip())
    if quote_available:
        qs = _token_f1(gt.verbatim_quote, ai.quote)
        combined = CLAIM_W * vs_lex + QUOTE_W * qs
    else:
        combined = vs_lex

    sem_score = sem.score(gt.value, ai.value) if sem is not None else 0.0

    return max(vs_lex, sem_score), qs, combined, sem_score


def _verdict(combined_lexical: float, semantic: float,
             sem_min: float = SEMANTIC_MIN) -> str:
    """Lexical drives the confident bands; semantic only rescues an otherwise
    missed pair into a flagged review (never a silent auto_match on an
    unvalidated threshold)."""
    if combined_lexical >= MATCH_THRESHOLD:
        return "auto_match"
    if combined_lexical >= REVIEW_THRESHOLD:
        return "review"
    if semantic >= sem_min:
        return "semantic_review"
    return "auto_miss"


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
def _clean(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v).strip()


def read_gt(filepath: str) -> list[GTRow]:
    xls = pd.ExcelFile(filepath)
    sheet = next((s for s in xls.sheet_names if s.lower() == "groundtruth"), None)
    if sheet is None:
        raise ValueError(
            f"GT workbook {filepath!r} has no 'GroundTruth' sheet. "
            f"Found: {xls.sheet_names}"
        )
    df = pd.read_excel(xls, sheet_name=sheet)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    rows = []
    for _, r in df.iterrows():
        entity  = _clean(r.get("entity"))
        question = _clean(r.get("question"))
        value   = _clean(r.get("value"))
        if not entity or not question or not value:
            continue
        is_list_raw = r.get("is_list", False)
        is_list = str(is_list_raw).strip().lower() not in ("false", "0", "no", "")
        rows.append(GTRow(
            entity=entity,
            entity_norm=_norm(entity),
            question=question,
            question_norm=_norm(question),
            value=value,
            is_list=is_list,
            verbatim_quote=_clean(r.get("verbatim_quote")),
            source_url=_clean(r.get("source_url")),
            notes=_clean(r.get("notes")),
            is_null=_is_null(value),
        ))
    return rows


def read_pipeline_output(filepath: str) -> list[AIRow]:
    """Read the Provenance sheet from a pipeline output workbook."""
    xls = pd.ExcelFile(filepath)
    prov_sheet = next(
        (s for s in xls.sheet_names if "provenance" in s.lower()), None
    )
    if prov_sheet is None:
        raise ValueError(
            f"Pipeline output {filepath!r} has no Provenance sheet. "
            f"Found: {xls.sheet_names}"
        )
    df = pd.read_excel(xls, sheet_name=prov_sheet)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Normalise column aliases. The Provenance schema was renamed after this
    # evaluator was first written (2026-07-07): the question column is now
    # "Question" (was "Column") and the quote column is "Verbatim Quote" (was
    # "Quote"). Both spellings are accepted so old and new workbooks evaluate.
    col_map = {
        "claim":    ["claim"],
        "column":   ["column", "question"],
        "entity":   ["entity"],
        "quote":    ["verbatim_quote", "quote"],
        "verified": ["verified"],
        "match_type": ["match_type", "match type"],
        "source_url": ["source_url", "source url"],
    }
    def _find(names: list[str]):
        for n in names:
            if n in df.columns:
                return n
        return None

    resolved = {k: _find(v) for k, v in col_map.items()}
    if not resolved["entity"] or not resolved["column"] or not resolved["claim"]:
        raise ValueError(
            f"Provenance sheet is missing required columns (entity/column/claim). "
            f"Found: {list(df.columns)}"
        )

    rows = []
    for _, r in df.iterrows():
        entity   = _clean(r.get(resolved["entity"]))
        question = _clean(r.get(resolved["column"]))
        value    = _clean(r.get(resolved["claim"]))
        if not entity or not question or not value:
            continue
        verified_raw = r.get(resolved["verified"]) if resolved["verified"] else False
        rows.append(AIRow(
            entity=entity,
            entity_norm=_norm(entity),
            question=question,
            question_norm=_norm(question),
            value=value,
            quote=_clean(r.get(resolved["quote"])) if resolved["quote"] else "",
            verified=str(verified_raw).strip().lower() in ("true", "1", "yes"),
            match_type=_clean(r.get(resolved["match_type"])) if resolved["match_type"] else "",
            source_url=_clean(r.get(resolved["source_url"])) if resolved["source_url"] else "",
        ))
    return rows


_MATRIX_MARKER_LINES = {"(sources conflict)", "(unverified)", "-- unverified --"}


def read_pipeline_matrix(filepath: str) -> list[AIRow]:
    """Read the deliverable MATRIX sheet instead of Provenance.

    Provenance mode measures what the pipeline EXTRACTED; matrix mode measures
    what the deliverable SHOWS — after aggregation, display capping
    (MATRIX_MAX_DISPLAY_ITEMS) and Excel clamping. Items hidden behind
    "[+N more items — see Provenance]" therefore count as missing here:
    that is the point of the mode, not a limitation.

    Cell grammar (io_excel._make_matrix_df):
      "No data found"                       -> null sentinel (true-negative
                                               against a null GT cell)
      "- value" lines                       -> one claim each, verified until
      "-- Unverified --"                    -> section switch: following
                                               bullets are unverified
      "(unverified)" (anywhere in cell)     -> whole cell unverified
      "(sources conflict)" / "[+N more...]" / "[truncated ...]" -> skipped
    Matrix cells carry no quotes, so the evaluator's quote signal never fires
    in this mode (as when GT has no quotes).
    """
    xls = pd.ExcelFile(filepath)
    sheet = next((s for s in xls.sheet_names if s.lower() == "matrix"), None)
    if sheet is None:
        raise ValueError(
            f"Pipeline output {filepath!r} has no Matrix sheet. "
            f"Found: {xls.sheet_names}"
        )
    df = pd.read_excel(xls, sheet_name=sheet)
    ent_col = next((c for c in df.columns if _norm(str(c)) == "entity"), df.columns[0])
    question_cols = [c for c in df.columns if c != ent_col]

    rows: list[AIRow] = []
    for _, r in df.iterrows():
        entity = _clean(r.get(ent_col))
        if not entity:
            continue
        for q in question_cols:
            question = str(q).strip()
            text = _clean(r.get(q))
            if not text:
                continue
            if _norm(text) == "no data found":
                rows.append(AIRow(
                    entity=entity, entity_norm=_norm(entity),
                    question=question, question_norm=_norm(question),
                    value="None (not disclosed)",  # scores as an AI null claim
                    quote="", verified=False, match_type="matrix", source_url="",
                ))
                continue
            verified = not any(
                _norm(line) == "(unverified)" for line in text.split("\n")
            )
            for line in text.split("\n"):
                s = line.strip()
                if not s:
                    continue
                sn = _norm(s)
                if sn == "-- unverified --":
                    verified = False
                    continue
                if sn in _MATRIX_MARKER_LINES or s.startswith("[+") or sn.startswith("[truncated"):
                    continue
                value = s[2:].strip() if s.startswith("- ") else s
                if not value:
                    continue
                rows.append(AIRow(
                    entity=entity, entity_norm=_norm(entity),
                    question=question, question_norm=_norm(question),
                    value=value, quote="", verified=verified,
                    match_type="matrix", source_url="",
                ))
    return rows


# ---------------------------------------------------------------------------
# Dedup AI claims per cell (keep best provenance per normalised value)
# ---------------------------------------------------------------------------
def _dedup_ai(ai: list[AIRow]) -> list[AIRow]:
    """Collapse near-duplicate AI claims to one representative each, keeping the
    best-provenance member. Near-duplicate = token_sort_ratio >= AI_DEDUP_RATIO
    (not just exact-string): "Geneva, Switzerland" and "based in Geneva" are the
    same claim and must not each count as a hallucination on the precision side
    (the plant-milk evaluator's rule, eval_lib/metrics.py)."""
    rank = {"exact": 3, "fuzzy": 2, "fuzzy_soft": 1, "none": 0}

    def _better(a: AIRow, b: AIRow) -> AIRow:
        ar = (a.verified, rank.get(a.match_type, 0))
        br = (b.verified, rank.get(b.match_type, 0))
        return a if ar > br else b

    reps: list[AIRow] = []
    rep_norms: list[str] = []
    for a in ai:
        key = _norm(a.value)
        hit = None
        for i, rn in enumerate(rep_norms):
            if key == rn or fuzz.token_sort_ratio(key, rn) >= AI_DEDUP_RATIO:
                hit = i
                break
        if hit is None:
            reps.append(a)
            rep_norms.append(key)
        else:
            reps[hit] = _better(reps[hit], a)
    return reps


# ---------------------------------------------------------------------------
# Cell-level alignment
# ---------------------------------------------------------------------------
def _align_cell(
    gt_rows: list[GTRow],
    ai_rows: list[AIRow],
    is_list: bool,
    sem=None,
) -> CellResult:
    entity   = gt_rows[0].entity if gt_rows else (ai_rows[0].entity if ai_rows else "?")
    question = gt_rows[0].question if gt_rows else (ai_rows[0].question if ai_rows else "?")

    ai_dedup = _dedup_ai(ai_rows)
    no_ai_at_all = len(ai_dedup) == 0

    gt_null  = [g for g in gt_rows if g.is_null]
    gt_real  = [g for g in gt_rows if not g.is_null]
    ai_null  = [a for a in ai_dedup if _is_null(a.value)]
    ai_real  = [a for a in ai_dedup if not _is_null(a.value)]

    pairs: list[PairResult] = []
    used_ai: set[int] = set()

    # ── real-claim greedy 1:1 matching ──────────────────────────────────────
    if gt_real and ai_real:
        # S[i][j] = (value_score, quote_score, combined_lexical, semantic)
        S: list[list[tuple[float, float, float, float]]] = [
            [_pair_score(g, a, sem) for a in ai_real]
            for g in gt_real
        ]
        # Semantic rescue is for single-answer PROSE cells (missions,
        # descriptions), where a paraphrase is the same answer. It is DISABLED
        # for list cells: named list items need identity, not similarity —
        # mean-centred nomic embeddings put all short proper nouns (Firefox,
        # Thunderbird, Common Voice) on nearly one axis, so cosine ~1 between
        # DISTINCT items would falsely credit one project for another
        # (observed on task1 Mozilla, 2026-07-15).
        sem_allowed = not is_list
        sem_min = sem.min_score if sem is not None else SEMANTIC_MIN

        candidates = []
        for i in range(len(gt_real)):
            for j in range(len(ai_real)):
                vs, qs, comb, sem_score = S[i][j]
                sem_ok = sem_allowed and sem_score >= sem_min
                strength = max(comb, sem_score if sem_ok else 0.0)
                if comb >= REVIEW_THRESHOLD or sem_ok:
                    candidates.append((strength, i, j))
        candidates.sort(reverse=True)

        used_gt: set[int] = set()
        group_to_ai: dict[int, int] = {}
        for strength, i, j in candidates:
            if i in used_gt or j in used_ai:
                continue
            group_to_ai[i] = j
            used_gt.add(i)
            used_ai.add(j)

        for i, g in enumerate(gt_real):
            if i in group_to_ai:
                j = group_to_ai[i]
                a = ai_real[j]
                vs, qs, comb, sem_score = S[i][j]
                pairs.append(PairResult(
                    gt_value=g.value, ai_value=a.value,
                    value_score=round(vs, 4), quote_score=round(qs, 4),
                    combined=round(comb, 4),
                    verdict=_verdict(comb, sem_score if sem_allowed else 0.0,
                                     sem_min),
                    semantic=round(sem_score, 4),
                ))
            else:
                pairs.append(PairResult(
                    gt_value=g.value, ai_value=None,
                    value_score=0, quote_score=0, combined=0,
                    verdict="no_ai_data" if no_ai_at_all else "auto_miss",
                ))
    else:
        for g in gt_real:
            pairs.append(PairResult(
                gt_value=g.value, ai_value=None,
                value_score=0, quote_score=0, combined=0,
                verdict="no_ai_data" if no_ai_at_all else "auto_miss",
            ))

    # ── null structural matching ─────────────────────────────────────────────
    remaining_null_ai = list(range(len(ai_null)))
    used_null_ai: set[int] = set()
    for g in gt_null:
        if remaining_null_ai:
            j = remaining_null_ai.pop(0)
            used_null_ai.add(j)
            pairs.append(PairResult(
                gt_value=g.value, ai_value=ai_null[j].value,
                value_score=1.0, quote_score=0, combined=1.0,
                verdict="null_match",
            ))
        else:
            pairs.append(PairResult(
                gt_value=g.value, ai_value=None,
                value_score=0, quote_score=0, combined=0,
                verdict="no_ai_data" if no_ai_at_all else "auto_miss",
            ))

    # ── AI-only leftovers: separate genuine extras (FP) from restatements ────
    # A leftover AI claim that is a near-duplicate (lexical >= AI_DEDUP_RATIO)
    # or a semantic match (cosine >= SEMANTIC_MIN) of an ALREADY-CREDITED AI
    # claim in this cell is a redundant restatement, not a new false claim —
    # e.g. "Geneva" alongside a matched "Geneva, Switzerland". It is dropped
    # from the precision denominator (the plant-milk "redundant" category),
    # not counted as a hallucination.
    matched_real = [ai_real[j] for j in used_ai]

    def _is_restatement(a: AIRow) -> bool:
        for m in matched_real:
            if fuzz.token_sort_ratio(_norm(a.value), _norm(m.value)) >= AI_DEDUP_RATIO:
                return True
            # Semantic "same claim" only for single-answer cells — on lists it
            # would wrongly merge distinct named items (see the matching note).
            if not is_list and sem is not None:
                if sem.score(a.value, m.value) >= sem.min_score:
                    return True
        return False

    ai_only: list[AIRow] = []
    redundant: list[AIRow] = []
    for j, a in enumerate(ai_real):
        if j in used_ai:
            continue
        (redundant if _is_restatement(a) else ai_only).append(a)
    # AI null claims that don't match any GT null are also precision-side
    for j, a in enumerate(ai_null):
        if j not in used_null_ai:
            ai_only.append(a)

    # For cells where GT has a null claim and AI extracted a REAL claim: hallucination
    if gt_null and not gt_real:
        ai_only.extend(ai_real)

    return CellResult(
        entity=entity, question=question, is_list=is_list,
        gt_pairs=pairs, ai_only=ai_only, redundant=redundant,
    )


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------
def evaluate(
    gt: list[GTRow],
    ai: list[AIRow],
    semantic: bool = True,
    semantic_backend: str = "ollama",
) -> EvalResult:
    # Build the semantic scorer ONCE for the whole run (for the embedding
    # backend that means one Ollama batch, then O(1) lookups during
    # alignment). None => lexical-only.
    sem = None
    if semantic:
        texts = [g.value for g in gt if not g.is_null]
        texts += [a.value for a in ai if not _is_null(a.value)]
        sem = _build_semantic(texts, semantic_backend)

    # Build cell index
    cells: dict[tuple[str, str], dict] = {}
    for g in gt:
        key = (g.entity_norm, g.question_norm)
        if key not in cells:
            cells[key] = {
                "entity": g.entity, "question": g.question,
                "is_list": g.is_list, "gt": [], "ai": [],
            }
        cells[key]["gt"].append(g)
        cells[key]["is_list"] = cells[key]["is_list"] or g.is_list

    # Try to align AI questions to GT questions (exact norm match, then fuzzy)
    gt_qnorms = {g.question_norm for g in gt}
    for a in ai:
        # First try exact norm match
        matched_q = a.question_norm if a.question_norm in gt_qnorms else None
        if matched_q is None:
            # Fuzzy match against known GT question norms
            best_q, best_s = None, 0.0
            for qn in gt_qnorms:
                s = fuzz.token_sort_ratio(a.question_norm, qn) / 100.0
                if s > best_s:
                    best_s = s
                    best_q = qn
            matched_q = best_q if best_s >= 0.70 else None

        if matched_q is None:
            continue  # unmapped AI question — skip

        key = (a.entity_norm, matched_q)
        if key not in cells:
            cells[key] = {
                "entity": a.entity, "question": a.question,
                "is_list": False, "gt": [], "ai": [],
            }
        cells[key]["ai"].append(a)

    results: list[CellResult] = []
    for key, slot in cells.items():
        if not slot["gt"] and not slot["ai"]:
            continue
        results.append(_align_cell(slot["gt"], slot["ai"], slot["is_list"], sem))

    results.sort(key=lambda c: (c.entity, c.question))

    # ── aggregate metrics ─────────────────────────────────────────────────
    _TP_VERDICTS = ("auto_match", "review", "semantic_review", "null_match")

    def _cell_counts(cell: CellResult) -> tuple[int, int, int]:
        tp = sum(1 for p in cell.gt_pairs if p.verdict in _TP_VERDICTS)
        fn = sum(
            1 for p in cell.gt_pairs
            if p.verdict in ("auto_miss", "no_ai_data")
        )
        fp = len(cell.ai_only)
        return tp, fn, fp

    def _metrics(tp: int, fn: int, fp: int) -> dict:
        r = tp / (tp + fn) if (tp + fn) > 0 else 1.0
        p = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        hall = fp / max(1, tp + fp)
        return {
            "TP": tp, "FN": fn, "FP": fp,
            "precision": round(p, 4),
            "recall":    round(r, 4),
            "F1":        round(f1, 4),
            "hallucination_rate": round(hall, 4),
        }

    per_question: dict[str, dict] = {}
    for cell in results:
        q = cell.question
        tp, fn, fp = _cell_counts(cell)
        if q not in per_question:
            per_question[q] = {"TP": 0, "FN": 0, "FP": 0, "cells": 0, "is_list": False}
        per_question[q]["TP"] += tp
        per_question[q]["FN"] += fn
        per_question[q]["FP"] += fp
        per_question[q]["cells"] += 1
        per_question[q]["is_list"] = per_question[q]["is_list"] or cell.is_list

    pq_metrics = {}
    for q, counts in per_question.items():
        m = _metrics(counts["TP"], counts["FN"], counts["FP"])
        m["cells"] = counts["cells"]
        m["is_list"] = counts["is_list"]
        pq_metrics[q] = m

    def _block(is_list_wanted: bool) -> dict:
        qs = [c for c in per_question.values() if c["is_list"] == is_list_wanted]
        return _metrics(sum(c["TP"] for c in qs), sum(c["FN"] for c in qs),
                        sum(c["FP"] for c in qs))

    total_tp = sum(c["TP"] for c in per_question.values())
    total_fn = sum(c["FN"] for c in per_question.values())
    total_fp = sum(c["FP"] for c in per_question.values())
    overall = _metrics(total_tp, total_fn, total_fp)
    overall["cells"] = len(results)
    overall["entities"] = len({c.entity for c in results})
    overall["semantic_rescues"] = sum(
        1 for c in results for p in c.gt_pairs if p.verdict == "semantic_review"
    )
    overall["redundant_dropped"] = sum(len(c.redundant) for c in results)
    # Split headline: single-answer questions are the TRUSTWORTHY metric; list
    # questions have non-exhaustive GT (the pipeline finds real items the GT
    # never enumerated), so their precision is only a LOWER BOUND — reported
    # separately, never mixed into the headline (George's decision 2026-07-16).
    overall["single"] = _block(False)
    overall["list"] = _block(True)

    return EvalResult(cells=results, per_question=pq_metrics, overall=overall)


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------
def print_report(result: EvalResult, verbose: bool = False) -> None:
    print()
    print("=" * 68)
    print(" GENERIC EVAL REPORT")
    print("=" * 68)

    def _qtable(is_list_wanted: bool):
        rows = [(q, m) for q, m in result.per_question.items()
                if m.get("is_list", False) == is_list_wanted]
        for q, m in rows:
            print(f"  {q[:38]:<38}  {m['precision']:6.3f}  {m['recall']:6.3f}  "
                  f"{m['F1']:6.3f}  {m['hallucination_rate']:6.3f}  {m['cells']}")
        return rows

    o = result.overall
    hdr = f"\n{'QUESTION':<40}  {'P':>6}  {'R':>6}  {'F1':>6}  {'HALL':>6}  cells"

    # ── Trustworthy headline: single-answer questions ──
    print("\n### SINGLE-ANSWER QUESTIONS (trustworthy) ###")
    print(hdr)
    print("-" * 68)
    if _qtable(False):
        s = o["single"]
        print("-" * 68)
        print(f"  {'SINGLE-ANSWER OVERALL':<38}  {s['precision']:6.3f}  {s['recall']:6.3f}  "
              f"{s['F1']:6.3f}  {s['hallucination_rate']:6.3f}  "
              f"TP={s['TP']} FN={s['FN']} FP={s['FP']}")
    else:
        print("  (none)")

    # ── List questions: precision is a LOWER BOUND (non-exhaustive GT) ──
    print("\n### LIST QUESTIONS — precision = LOWER BOUND (GT non-exhaustive) ###")
    print("  Unmatched AI items may be real-but-unlisted, not hallucinations.")
    print(hdr)
    print("-" * 68)
    if _qtable(True):
        ls = o["list"]
        print("-" * 68)
        print(f"  {'LIST OVERALL (P=lower bound)':<38}  {ls['precision']:6.3f}  {ls['recall']:6.3f}  "
              f"{ls['F1']:6.3f}  {ls['hallucination_rate']:6.3f}  "
              f"TP={ls['TP']} FN={ls['FN']} FP={ls['FP']}")
    else:
        print("  (none)")

    print("\n" + "-" * 68)
    print(f"  COMBINED (all questions): P={o['precision']:.3f} R={o['recall']:.3f} "
          f"F1={o['F1']:.3f} HALL={o['hallucination_rate']:.3f}  "
          f"{o['cells']} cells / {o['entities']} entities")
    print(f"  TP={o['TP']}  FN={o['FN']}  FP={o['FP']}")
    if o.get("semantic_rescues"):
        print(f"  ({o['semantic_rescues']} of those TP were semantic rescues — "
              f"lexically missed, matched by meaning; verdict 'semantic_review', "
              f"inspect in the Detail sheet)")
    if o.get("redundant_dropped"):
        print(f"  ({o['redundant_dropped']} AI claim(s) dropped as redundant "
              f"restatements of a credited claim — not counted as hallucination)")

    if verbose:
        print()
        print("-" * 68)
        print(" CELL-LEVEL DETAIL")
        print("-" * 68)
        for cell in result.cells:
            q_type = "list" if cell.is_list else "single"
            print(f"\n  [{cell.entity} / {cell.question}]  ({q_type})")
            for p in cell.gt_pairs:
                ai_str = repr(p.ai_value[:50]) if p.ai_value else "(none)"
                print(f"    [{p.verdict:15}] GT {repr(p.gt_value[:45])}")
                print(f"               -> AI {ai_str}  "
                      f"V={p.value_score:.2f} Q={p.quote_score:.2f} "
                      f"C={p.combined:.2f} S={p.semantic:.2f}")
            for a in cell.ai_only:
                ver = "✓" if a.verified else "✗"
                print(f"    [ai_only   ] {ver} AI {repr(a.value[:50])}")
    print()


# ---------------------------------------------------------------------------
# Optional Excel output
# ---------------------------------------------------------------------------
def write_report_excel(result: EvalResult, output_path: str) -> None:
    summary_rows = []
    for q, m in result.per_question.items():
        summary_rows.append({
            "question": q, "cells": m["cells"],
            "TP": m["TP"], "FN": m["FN"], "FP": m["FP"],
            "precision": m["precision"], "recall": m["recall"],
            "F1": m["F1"], "hallucination_rate": m["hallucination_rate"],
        })
    summary_rows.append({
        "question": "OVERALL", "cells": result.overall["cells"],
        "TP": result.overall["TP"], "FN": result.overall["FN"],
        "FP": result.overall["FP"],
        "precision": result.overall["precision"],
        "recall": result.overall["recall"],
        "F1": result.overall["F1"],
        "hallucination_rate": result.overall["hallucination_rate"],
    })

    detail_rows = []
    for cell in result.cells:
        for p in cell.gt_pairs:
            detail_rows.append({
                "entity": cell.entity, "question": cell.question,
                "is_list": cell.is_list,
                "gt_value": p.gt_value, "ai_value": p.ai_value or "",
                "value_score": p.value_score, "quote_score": p.quote_score,
                "semantic": p.semantic, "combined": p.combined,
                "verdict": p.verdict,
            })
        for a in cell.ai_only:
            detail_rows.append({
                "entity": cell.entity, "question": cell.question,
                "is_list": cell.is_list,
                "gt_value": "", "ai_value": a.value,
                "value_score": 0, "quote_score": 0, "semantic": 0, "combined": 0,
                "verdict": "ai_only",
            })
        for a in cell.redundant:
            detail_rows.append({
                "entity": cell.entity, "question": cell.question,
                "is_list": cell.is_list,
                "gt_value": "", "ai_value": a.value,
                "value_score": 0, "quote_score": 0, "semantic": 0, "combined": 0,
                "verdict": "redundant",
            })

    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        pd.DataFrame(summary_rows).to_excel(w, sheet_name="Summary", index=False)
        pd.DataFrame(detail_rows).to_excel(w, sheet_name="Detail", index=False)

    print(f"Report written to {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generic evaluation: compare pipeline output to flat GT workbook."
    )
    parser.add_argument("ground_truth",  help="Path to ground_truth.xlsx")
    parser.add_argument("pipeline_output", help="Path to pipeline output workbook")
    parser.add_argument("--output", help="Optional path for Excel report output")
    parser.add_argument("--verbose", action="store_true",
                        help="Print cell-level alignment detail")
    parser.add_argument("--no-semantic", action="store_true",
                        help="Disable embedding-based semantic matching (lexical only)")
    parser.add_argument("--semantic-backend", choices=["ollama", "cross-encoder"],
                        default="ollama",
                        help="Semantic matcher: 'ollama' = mean-centred "
                             "nomic-embed cosine (default); 'cross-encoder' = "
                             "local pairwise cross-encoder (EXPERIMENTAL — "
                             "threshold unvalidated, see src/eval/cross_encoder.py)")
    parser.add_argument("--sheet", choices=["provenance", "matrix"],
                        default="provenance",
                        help="Which pipeline sheet to score: 'provenance' = what "
                             "was extracted (default); 'matrix' = what the "
                             "deliverable shows (post-aggregation, post-display-cap)")
    args = parser.parse_args()

    print(f"GT      : {args.ground_truth}")
    print(f"Pipeline: {args.pipeline_output}  [{args.sheet} mode]")

    gt = read_gt(args.ground_truth)
    if args.sheet == "matrix":
        ai = read_pipeline_matrix(args.pipeline_output)
    else:
        ai = read_pipeline_output(args.pipeline_output)
    print(f"Loaded  : {len(gt)} GT rows, {len(ai)} AI claims")

    result = evaluate(gt, ai, semantic=not args.no_semantic,
                      semantic_backend=args.semantic_backend)
    print_report(result, verbose=args.verbose)

    if args.output:
        write_report_excel(result, args.output)


if __name__ == "__main__":
    main()
