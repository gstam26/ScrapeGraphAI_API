"""
Pipeline-output reader for the extraction evaluation framework (Stage 10 / RQ3).

Parses a pipeline output workbook (written by src/io_excel.py) into:

  - AIClaim records, from the **Provenance** sheet (the per-claim grain:
    claim, verbatim quote, verified, match_type, verification/semantic score,
    source url, source depth).
  - MatrixCell records, from the **Matrix** sheet (the aggregated deliverable:
    one rendered cell per entity x question, parsed back into status + values).

The scorer aligns at the claim grain (Provenance) and reports against the
aggregated deliverable (Matrix); both views are returned here.

IMPORTANT — char_span is NOT present in the Excel Provenance sheet (io_excel's
_make_provenance_df does not emit it). Sub-page localisation (metric 2.7) therefore
cannot be computed from the workbook alone. `PipelineOutput.has_char_span` reports
False so downstream code can flag the gap rather than silently scoring 0. Resolving
this needs either a new Provenance column or a JSON sidecar from the pipeline.

This module performs NO matching and NO embedding — pure I/O plus Matrix-cell
text parsing. Run it directly to self-check parsing:

    python diagnostics/eval_lib/pipeline_reader.py path/to/pipeline_output.xlsx
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# --- repo-root bootstrap -----------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from src.aggregate import _is_null_sentinel, _normalise_value
from src.io_excel import _clean_str, _find_column, _find_sheet

# Reuse the GT-side entity normaliser so both sources canonicalise identically.
from diagnostics.eval_lib.gt_reader import normalise_entity


# Matrix-cell control strings emitted by io_excel._make_matrix_df.
_NO_DATA = "no data found"
_CONFLICT_MARK = "(sources conflict)"
_UNVERIFIED_SECTION = "-- unverified --"
_UNVERIFIED_TAG = "(unverified)"


@dataclass
class AIClaim:
    """One pipeline-extracted claim, from the Provenance grain."""
    entity: str
    entity_norm: str
    question: str               # pipeline question NAME (Provenance "Question" column)
    value: str                  # the claim text
    quote: str                  # verbatim supporting quote ("" if none)
    source_url: str
    verified: bool              # read from pipeline; NOT recomputed
    match_type: str             # exact | fuzzy | none; read from pipeline
    verification_score: float | None
    semantic_score: float | None  # value-vs-own-quote (NOT a GT-matching signal)
    confidence_score: float | None
    source_depth: int
    is_null: bool               # value is the "None (not disclosed…)" sentinel
    char_span: tuple[int, int] | None = None  # absent from Excel -> always None here


@dataclass
class MatrixCell:
    """One aggregated deliverable cell, parsed back from the rendered Matrix text."""
    entity: str
    entity_norm: str
    question: str
    status: str                 # "data" | "no_data" | "conflict"
    values: list[str]           # displayed distinct values (verified + unverified)
    raw_text: str


@dataclass
class PipelineOutput:
    ai_claims: list[AIClaim]
    matrix: list[MatrixCell]
    questions: list[str]                       # question names seen in the Matrix header
    has_char_span: bool = False                # False when sourced from Excel Provenance

    def claims_for(self, entity_norm: str, question: str) -> list[AIClaim]:
        return [
            c for c in self.ai_claims
            if c.entity_norm == entity_norm and c.question == question
        ]

    def matrix_cell(self, entity_norm: str, question: str) -> MatrixCell | None:
        return next(
            (m for m in self.matrix
             if m.entity_norm == entity_norm and m.question == question),
            None,
        )

    def entities(self) -> list[str]:
        seen: dict[str, str] = {}
        for c in self.ai_claims:
            seen.setdefault(c.entity_norm, c.entity)
        for m in self.matrix:
            seen.setdefault(m.entity_norm, m.entity)
        return [seen[k] for k in sorted(seen)]

    def cells(self) -> list[tuple[str, str]]:
        seen = {(c.entity_norm, c.question) for c in self.ai_claims}
        seen |= {(m.entity_norm, m.question) for m in self.matrix}
        return sorted(seen)


# --- helpers -----------------------------------------------------------------
def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _parse_matrix_cell_text(text: str) -> tuple[str, list[str]]:
    """Reverse io_excel's Matrix rendering into (status, values).

    Renderings handled (see _make_matrix_df):
      "No data found"                              -> ("no_data", [])
      "(sources conflict)\n- a\n- b"               -> ("conflict", [a, b])
      "- a\n- b" / "- a\n(unverified)"             -> ("data", [a, b])
    Section markers ("-- Unverified --", "(unverified)") are dropped; the values
    themselves (verified + unverified) are all collected.
    """
    raw = _clean_str(text)
    low = raw.lower()
    if not raw or _NO_DATA in low:
        return "no_data", []

    status = "conflict" if _CONFLICT_MARK in low else "data"
    values: list[str] = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        sl = s.lower()
        if sl.startswith(_CONFLICT_MARK) or sl == _UNVERIFIED_SECTION or sl == _UNVERIFIED_TAG:
            continue
        if s.startswith("- "):
            s = s[2:].strip()
        if s:
            values.append(s)
    return status, values


# --- sheet parsers -----------------------------------------------------------
def _read_provenance(xls: pd.ExcelFile, sheet_name: str) -> list[AIClaim]:
    df = pd.read_excel(xls, sheet_name=sheet_name)

    col = {
        "entity": _find_column(df, "Entity"),
        "source_url": _find_column(df, "Source URL"),
        "question": _find_column(df, "Question"),
        "claim": _find_column(df, "Claim"),
        "quote": _find_column(df, "Verbatim Quote"),
        "confidence": _find_column(df, "Confidence Score"),
        "verified": _find_column(df, "Verified"),
        "ver_score": _find_column(df, "Verification Score"),
        "match_type": _find_column(df, "Match Type"),
        "sem_score": _find_column(df, "Semantic Score"),
        "depth": _find_column(df, "Source Page Depth"),
    }
    if col["entity"] is None or col["question"] is None or col["claim"] is None:
        raise ValueError(
            f"Provenance sheet {sheet_name!r}: missing Entity/Question/Claim columns. "
            f"Found: {list(df.columns)}"
        )

    claims: list[AIClaim] = []
    for _, row in df.iterrows():
        value = _clean_str(row.get(col["claim"]))
        if not value:
            continue
        entity = _clean_str(row.get(col["entity"]))
        depth_raw = _to_float(row.get(col["depth"])) if col["depth"] else None
        claims.append(AIClaim(
            entity=entity,
            entity_norm=normalise_entity(entity),
            question=_clean_str(row.get(col["question"])),
            value=value,
            quote=_clean_str(row.get(col["quote"])) if col["quote"] else "",
            source_url=_clean_str(row.get(col["source_url"])) if col["source_url"] else "",
            verified=_to_bool(row.get(col["verified"])) if col["verified"] else False,
            match_type=(_clean_str(row.get(col["match_type"])).lower() or "none") if col["match_type"] else "none",
            verification_score=_to_float(row.get(col["ver_score"])) if col["ver_score"] else None,
            semantic_score=_to_float(row.get(col["sem_score"])) if col["sem_score"] else None,
            confidence_score=_to_float(row.get(col["confidence"])) if col["confidence"] else None,
            source_depth=int(depth_raw) if depth_raw is not None else 0,
            is_null=_is_null_sentinel(_normalise_value(value)),
        ))
    return claims


def _read_matrix(xls: pd.ExcelFile, sheet_name: str) -> tuple[list[MatrixCell], list[str]]:
    df = pd.read_excel(xls, sheet_name=sheet_name)
    entity_col = _find_column(df, "Entity") or (df.columns[0] if len(df.columns) else None)
    if entity_col is None:
        raise ValueError(f"Matrix sheet {sheet_name!r} has no columns")

    question_cols = [c for c in df.columns if c != entity_col]
    cells: list[MatrixCell] = []
    for _, row in df.iterrows():
        entity = _clean_str(row.get(entity_col))
        if not entity:
            continue
        for qcol in question_cols:
            status, values = _parse_matrix_cell_text(row.get(qcol))
            cells.append(MatrixCell(
                entity=entity,
                entity_norm=normalise_entity(entity),
                question=str(qcol).strip(),
                status=status,
                values=values,
                raw_text=_clean_str(row.get(qcol)),
            ))
    return cells, [str(c).strip() for c in question_cols]


def read_pipeline_output(filepath: str) -> PipelineOutput:
    """Parse a pipeline output workbook (Matrix + Provenance)."""
    xls = pd.ExcelFile(filepath)
    try:
        prov_sheet = _find_sheet(xls, "Provenance")
        matrix_sheet = _find_sheet(xls, "Matrix")
        if prov_sheet is None:
            raise ValueError(
                f"Pipeline output missing a 'Provenance' sheet. Found: {xls.sheet_names}"
            )
        if matrix_sheet is None:
            raise ValueError(
                f"Pipeline output missing a 'Matrix' sheet. Found: {xls.sheet_names}"
            )
        ai_claims = _read_provenance(xls, prov_sheet)
        matrix, questions = _read_matrix(xls, matrix_sheet)
    finally:
        xls.close()

    return PipelineOutput(
        ai_claims=ai_claims,
        matrix=matrix,
        questions=questions,
        has_char_span=False,  # Excel Provenance carries no char_span column
    )


def crosscheck_entities(gt_entities: list[str], pipe_entities: list[str]) -> dict:
    """Edge case 7.3 — align GT and pipeline entities after normalisation.

    Returns dict with: matched (list of norm keys), gt_only, pipe_only.
    Inputs are raw entity names from each source; normalisation is applied here.
    """
    gt_map = {normalise_entity(e): e for e in gt_entities}
    pipe_map = {normalise_entity(e): e for e in pipe_entities}
    gt_keys, pipe_keys = set(gt_map), set(pipe_map)
    return {
        "matched": sorted(gt_keys & pipe_keys),
        "gt_only": [gt_map[k] for k in sorted(gt_keys - pipe_keys)],
        "pipe_only": [pipe_map[k] for k in sorted(pipe_keys - gt_keys)],
    }


# --- self-check (verification items 2 & 3) -----------------------------------
def _selfcheck(filepath: str, gt_path: str | None = None) -> None:
    pipe = read_pipeline_output(filepath)

    print(f"\n=== pipeline_reader self-check: {filepath} ===\n")
    print(f"questions in Matrix header: {pipe.questions}")
    print(f"entities: {len(pipe.entities())} | provenance claims: {len(pipe.ai_claims)} | "
          f"matrix cells: {len(pipe.matrix)}")
    print(f"has_char_span (from Excel): {pipe.has_char_span}  "
          f"<- localisation metric needs a Provenance char_span column or JSON sidecar\n")

    print("--- AI claim counts per (entity, question) [Provenance grain] ---")
    for entity_norm in sorted({c.entity_norm for c in pipe.ai_claims}):
        label = next(c.entity for c in pipe.ai_claims if c.entity_norm == entity_norm)
        for q in pipe.questions:
            n = len(pipe.claims_for(entity_norm, q))
            mc = pipe.matrix_cell(entity_norm, q)
            status = mc.status if mc else "?"
            print(f"  {label} / {q}: {n} provenance claims (matrix status: {status})")

    print("\n--- example Provenance grain for one claim ---")
    example = next((c for c in pipe.ai_claims if c.quote), pipe.ai_claims[0] if pipe.ai_claims else None)
    if example is None:
        print("  (no provenance claims found)")
    else:
        print(f"  entity        : {example.entity}")
        print(f"  question      : {example.question}")
        print(f"  value (claim) : {example.value!r}")
        print(f"  quote         : {example.quote!r}")
        print(f"  verified      : {example.verified}")
        print(f"  match_type    : {example.match_type}")
        print(f"  ver_score     : {example.verification_score}")
        print(f"  semantic_score: {example.semantic_score}  (value-vs-own-quote, NOT a GT signal)")
        print(f"  source_url    : {example.source_url}")
        print(f"  char_span     : {example.char_span}  (None - absent from Excel Provenance)")

    if gt_path:
        from diagnostics.eval_lib.gt_reader import read_ground_truth
        gt = read_ground_truth(gt_path)
        result = crosscheck_entities(gt.entities(), pipe.entities())
        print("\n--- entity-name alignment (edge case 7.3) ---")
        print(f"  matched (both sources): {len(result['matched'])}")
        print(f"  in GT only (missing from pipeline output): {result['gt_only'] or '(none)'}")
        print(f"  in pipeline only (missing from GT)       : {result['pipe_only'] or '(none)'}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python diagnostics/eval_lib/pipeline_reader.py "
              "<pipeline_output.xlsx> [ground_truth.xlsx]")
        sys.exit(2)
    _selfcheck(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
