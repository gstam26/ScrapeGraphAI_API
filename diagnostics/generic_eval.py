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
  python diagnostics/generic_eval.py <ground_truth.xlsx> <pipeline_output.xlsx>
  python diagnostics/generic_eval.py <gt.xlsx> <pipe.xlsx> --output report.xlsx
  python diagnostics/generic_eval.py <gt.xlsx> <pipe.xlsx> --verbose
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    verdict: str   # auto_match | review | auto_miss | null_match | no_ai_data


@dataclass
class CellResult:
    entity: str
    question: str
    is_list: bool
    gt_pairs: list[PairResult]
    ai_only: list[AIRow]    # AI claims not matched to any GT


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


def _pair_score(gt: GTRow, ai: AIRow) -> tuple[float, float, float]:
    """Return (value_score, quote_score, combined)."""
    vs = fuzz.token_sort_ratio(_norm(gt.value), _norm(ai.value)) / 100.0
    qs = 0.0
    quote_available = bool(gt.verbatim_quote.strip()) and bool(ai.quote.strip())
    if quote_available:
        qs = _token_f1(gt.verbatim_quote, ai.quote)
        combined = CLAIM_W * vs + QUOTE_W * qs
    else:
        combined = vs
    return vs, qs, combined


def _verdict(combined: float) -> str:
    if combined >= MATCH_THRESHOLD:
        return "auto_match"
    if combined >= REVIEW_THRESHOLD:
        return "review"
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

    # Normalise column aliases
    col_map = {
        "claim":    ["claim"],
        "column":   ["column"],
        "entity":   ["entity"],
        "quote":    ["quote"],
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


# ---------------------------------------------------------------------------
# Dedup AI claims per cell (keep best provenance per normalised value)
# ---------------------------------------------------------------------------
def _dedup_ai(ai: list[AIRow]) -> list[AIRow]:
    rank = {"exact": 3, "fuzzy": 2, "fuzzy_soft": 1, "none": 0}
    best: dict[str, AIRow] = {}
    for a in ai:
        key = _norm(a.value)
        cur = best.get(key)
        if cur is None:
            best[key] = a
        else:
            a_rank = (a.verified, rank.get(a.match_type, 0))
            c_rank = (cur.verified, rank.get(cur.match_type, 0))
            if a_rank > c_rank:
                best[key] = a
    return list(best.values())


# ---------------------------------------------------------------------------
# Cell-level alignment
# ---------------------------------------------------------------------------
def _align_cell(
    gt_rows: list[GTRow],
    ai_rows: list[AIRow],
    is_list: bool,
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
        S: list[list[tuple[float, float, float]]] = [
            [_pair_score(g, a) for a in ai_real]
            for g in gt_real
        ]
        candidates = []
        for i in range(len(gt_real)):
            for j in range(len(ai_real)):
                vs, qs, comb = S[i][j]
                if comb >= REVIEW_THRESHOLD:
                    candidates.append((comb, i, j))
        candidates.sort(reverse=True)

        used_gt: set[int] = set()
        group_to_ai: dict[int, int] = {}
        for comb, i, j in candidates:
            if i in used_gt or j in used_ai:
                continue
            group_to_ai[i] = j
            used_gt.add(i)
            used_ai.add(j)

        for i, g in enumerate(gt_real):
            if i in group_to_ai:
                j = group_to_ai[i]
                a = ai_real[j]
                vs, qs, comb = S[i][j]
                pairs.append(PairResult(
                    gt_value=g.value, ai_value=a.value,
                    value_score=round(vs, 4), quote_score=round(qs, 4),
                    combined=round(comb, 4), verdict=_verdict(comb),
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

    # ── AI-only leftovers ────────────────────────────────────────────────────
    ai_only: list[AIRow] = []
    for j, a in enumerate(ai_real):
        if j not in used_ai:
            ai_only.append(a)
    # AI null claims that don't match any GT null are also precision-side
    for j, a in enumerate(ai_null):
        if j not in used_null_ai:
            ai_only.append(a)

    # For cells where GT has a null claim and AI extracted a REAL claim: hallucination
    if gt_null and not gt_real:
        ai_only.extend(ai_real)

    return CellResult(
        entity=entity, question=question, is_list=is_list,
        gt_pairs=pairs, ai_only=ai_only,
    )


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------
def evaluate(gt: list[GTRow], ai: list[AIRow]) -> EvalResult:
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
        results.append(_align_cell(slot["gt"], slot["ai"], slot["is_list"]))

    results.sort(key=lambda c: (c.entity, c.question))

    # ── aggregate metrics ─────────────────────────────────────────────────
    def _cell_counts(cell: CellResult) -> tuple[int, int, int]:
        tp = sum(
            1 for p in cell.gt_pairs
            if p.verdict in ("auto_match", "review", "null_match")
        )
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
            per_question[q] = {"TP": 0, "FN": 0, "FP": 0, "cells": 0}
        per_question[q]["TP"] += tp
        per_question[q]["FN"] += fn
        per_question[q]["FP"] += fp
        per_question[q]["cells"] += 1

    pq_metrics = {}
    for q, counts in per_question.items():
        m = _metrics(counts["TP"], counts["FN"], counts["FP"])
        m["cells"] = counts["cells"]
        pq_metrics[q] = m

    total_tp = sum(c["TP"] for c in per_question.values())
    total_fn = sum(c["FN"] for c in per_question.values())
    total_fp = sum(c["FP"] for c in per_question.values())
    overall = _metrics(total_tp, total_fn, total_fp)
    overall["cells"] = len(results)
    overall["entities"] = len({c.entity for c in results})

    return EvalResult(cells=results, per_question=pq_metrics, overall=overall)


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------
def print_report(result: EvalResult, verbose: bool = False) -> None:
    print()
    print("=" * 68)
    print(" GENERIC EVAL REPORT")
    print("=" * 68)

    print(f"\n{'QUESTION':<40}  {'P':>6}  {'R':>6}  {'F1':>6}  {'HALL':>6}  cells")
    print("-" * 68)
    for q, m in result.per_question.items():
        label = q[:39]
        print(f"  {label:<38}  {m['precision']:6.3f}  {m['recall']:6.3f}  "
              f"{m['F1']:6.3f}  {m['hallucination_rate']:6.3f}  {m['cells']}")

    o = result.overall
    print("-" * 68)
    print(f"  {'OVERALL':<38}  {o['precision']:6.3f}  {o['recall']:6.3f}  "
          f"{o['F1']:6.3f}  {o['hallucination_rate']:6.3f}  "
          f"{o['cells']} cells / {o['entities']} entities")
    print(f"  TP={o['TP']}  FN={o['FN']}  FP={o['FP']}")

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
                print(f"    [{p.verdict:10}] GT {repr(p.gt_value[:45])}")
                print(f"               -> AI {ai_str}  "
                      f"V={p.value_score:.2f} Q={p.quote_score:.2f} C={p.combined:.2f}")
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
                "combined": p.combined, "verdict": p.verdict,
            })
        for a in cell.ai_only:
            detail_rows.append({
                "entity": cell.entity, "question": cell.question,
                "is_list": cell.is_list,
                "gt_value": "", "ai_value": a.value,
                "value_score": 0, "quote_score": 0, "combined": 0,
                "verdict": "ai_only",
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
    args = parser.parse_args()

    print(f"GT      : {args.ground_truth}")
    print(f"Pipeline: {args.pipeline_output}")

    gt  = read_gt(args.ground_truth)
    ai  = read_pipeline_output(args.pipeline_output)
    print(f"Loaded  : {len(gt)} GT rows, {len(ai)} AI claims")

    result = evaluate(gt, ai)
    print_report(result, verbose=args.verbose)

    if args.output:
        write_report_excel(result, args.output)


if __name__ == "__main__":
    main()
