"""
Ground-truth reader for the extraction evaluation framework (Stage 10 / RQ3).

Parses the analyst-built ground-truth workbook into a normalised internal model
and exposes:

  - active_claims : the recall denominator. This is simply the rows of the three
                    question sheets AS-IS. The question sheets are already
                    semantically pre-cleaned by the analyst, so NOTHING is
                    subtracted here (e.g. 86 Sustainability claims stay 86).

  - excluded      : the Excluded audit tab, loaded as flat ExcludedItem records
                    grouped by category. Excluded rows reference claims by their
                    `excluded_text` sentence (NOT by claim_id) and are NOT present
                    in the question sheets. They are used by the aligner for
                    PRECISION-side scoring only — never to filter the GT:
                      inclusion_bar   -> AI claim matching one counts against precision
                      dynamic_counter -> NEUTRAL: drop the matching AI claim from the
                                         precision denominator
                      duplicate       -> maps to a canonical claim: credit, don't
                                         double-count

Expected sheets (column matching is case/whitespace tolerant via _find_column):

  Sustainability : entity, claim_id, quote_id, claim, verbatim_quote,
                   type, dimension, source_url, flag, notes
  MilkTypes      : entity, milk_type, source_url
  ParentCompany  : entity, parent_company, disclosure_quote, source_url, notes
  Excluded       : entity, excluded_text, category, rationale, scoring_treatment
                   -- NB: real headers are NOT on the first row. The sheet opens
                      with a title + legend; the header row is detected, not assumed.
  Flags          : resolved-status log (read for completeness; not scored here)

This module performs NO matching and NO embedding. Run it directly to self-check:

    python src/eval/gt_reader.py path/to/ground_truth.xlsx
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

# --- repo-root bootstrap so `src` / `models` import whatever the cwd ---------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from src.aggregate import _is_null_sentinel, _normalise_value
from src.io_excel import _clean_str, _find_column, _find_sheet


# --- canonical question keys + mapping to pipeline question names ------------
# The GT workbook names its question sheets with these canonical keys; the
# pipeline names the same questions differently (free-text from the input
# workbook's `questions` sheet). The aligner uses GT_SHEET_TO_QUESTION to bridge
# the two. Hard-coded by design — no fuzzy matching.
Q_SUSTAINABILITY = "Sustainability"
Q_MILK_TYPES = "MilkTypes"
Q_PARENT_COMPANY = "ParentCompany"

GT_SHEET_TO_QUESTION = {
    "Sustainability": "Sustainability claims",
    "MilkTypes": "Plant milk types",
    "ParentCompany": "Parent company",
}

# Questions answered as a set of claims (recall over the set) vs a single answer.
_LIST_QUESTIONS = frozenset({Q_SUSTAINABILITY, Q_MILK_TYPES})

_EXCLUDED_CATEGORIES = frozenset({"inclusion_bar", "dynamic_counter", "duplicate"})


# --- normalisation helpers (shared with pipeline_reader for cross-source align) ---
def normalise_entity(name: str) -> str:
    """Canonicalise an entity name for cross-source matching (edge case 7.3).

    lower -> strip -> collapse internal whitespace -> strip surrounding
    punctuation. Conservative: fixes case/spacing/trailing-dot drift between GT
    and pipeline output without merging genuinely distinct names.
    """
    text = " ".join(str(name).strip().lower().split())
    return text.strip(" .,:;’'\"")


# --- data model --------------------------------------------------------------
@dataclass
class GTClaim:
    """One atomic ground-truth claim, normalised across the three question sheets."""
    entity: str                 # verbatim as written in the GT
    entity_norm: str            # normalise_entity(entity)
    question: str               # canonical key: Q_SUSTAINABILITY / Q_MILK_TYPES / Q_PARENT_COMPANY
    claim_id: str               # stable id (synthesised when the sheet lacks one)
    quote_id: str | None        # shared-quote group key; None when absent
    claim: str                  # the claim text / value
    verbatim_quote: str         # supporting quote, "" when absent
    source_url: str
    is_null: bool               # True when `claim` is the "None (not disclosed…)" sentinel
    is_list: bool               # True for list-type questions
    type: str = ""              # slice tag (Sustainability only); never enters scoring
    dimension: str = ""         # slice tag (Sustainability only); never enters scoring
    flag: str = ""
    notes: str = ""


@dataclass
class ExcludedItem:
    """One row from the Excluded audit tab — a precision-side reference, not a GT claim."""
    entity: str
    entity_norm: str
    excluded_text: str          # the sentence the analyst excluded
    excluded_text_norm: str     # _normalise_value(excluded_text) for matching
    category: str               # inclusion_bar | dynamic_counter | duplicate
    rationale: str
    scoring_treatment: str      # analyst's stated treatment (carried verbatim)


@dataclass
class GroundTruth:
    """Parsed ground truth: active claims (recall denominator) + Excluded audit lists."""
    active_claims: list[GTClaim]
    excluded: list[ExcludedItem]
    quote_id_groups: dict[str, list[str]] = field(default_factory=dict)  # quote_id -> [claim_id...]
    flags: list[dict] = field(default_factory=list)

    # ---- accessors used downstream / by the self-check ----
    def cells(self) -> list[tuple[str, str]]:
        """Sorted unique (entity_norm, question) pairs present in the active set."""
        return sorted({(c.entity_norm, c.question) for c in self.active_claims})

    def active_for(self, entity_norm: str, question: str) -> list[GTClaim]:
        return [
            c for c in self.active_claims
            if c.entity_norm == entity_norm and c.question == question
        ]

    def excluded_by_category(self) -> dict[str, list[ExcludedItem]]:
        """Three lists the aligner checks AI claims against (precision-side)."""
        out: dict[str, list[ExcludedItem]] = {cat: [] for cat in sorted(_EXCLUDED_CATEGORIES)}
        for e in self.excluded:
            out.setdefault(e.category, []).append(e)
        return out

    def entities(self) -> list[str]:
        seen: dict[str, str] = {}
        for c in self.active_claims:
            seen.setdefault(c.entity_norm, c.entity)
        return [seen[k] for k in sorted(seen)]


# --- per-sheet column maps ---------------------------------------------------
# Each tuple: (canonical field, list of acceptable header spellings). _find_column
# already tolerates case + surrounding whitespace; we list only different spellings.
_SUSTAINABILITY_COLS = {
    "entity": ["entity"],
    "claim_id": ["claim_id", "claim id"],
    "quote_id": ["quote_id", "quote id"],
    "claim": ["claim"],
    "verbatim_quote": ["verbatim_quote", "verbatim quote", "supporting quote"],
    "type": ["type"],
    "dimension": ["dimension"],
    "source_url": ["source_url", "source url"],
    "flag": ["flag"],
    "notes": ["notes"],
}
_MILK_COLS = {
    "entity": ["entity"],
    "claim": ["milk_type", "milk type", "claim"],
    "source_url": ["source_url", "source url"],
    "claim_id": ["claim_id", "claim id"],          # optional
    "quote_id": ["quote_id", "quote id"],          # optional
    "verbatim_quote": ["verbatim_quote", "verbatim quote", "supporting quote"],  # optional
    "notes": ["notes"],
}
_PARENT_COLS = {
    "entity": ["entity"],
    "claim": ["parent_company", "parent company", "claim"],
    "verbatim_quote": ["disclosure_quote", "disclosure quote", "verbatim_quote", "supporting quote"],
    "source_url": ["source_url", "source url"],
    "claim_id": ["claim_id", "claim id"],          # optional
    "quote_id": ["quote_id", "quote id"],          # optional
    "notes": ["notes"],
}
_EXCLUDED_COLS = {
    "entity": ["entity"],
    "excluded_text": ["excluded_text", "excluded text", "statement", "text"],
    "category": ["category"],
    "rationale": ["rationale", "reason"],
    "scoring_treatment": ["scoring_treatment", "scoring treatment", "treatment"],
}


def _resolve(df: pd.DataFrame, spellings: list[str]) -> str | None:
    """Return the actual DataFrame column matching any acceptable spelling."""
    for spelling in spellings:
        col = _find_column(df, spelling)
        if col is not None:
            return col
    return None


def _entity_slug(entity: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", entity).strip("-") or "ENT"


def _read_question_sheet(
    xls: pd.ExcelFile,
    sheet_name: str,
    question: str,
    colmap: dict[str, list[str]],
    id_prefix: str,
) -> list[GTClaim]:
    """Parse one question sheet into GTClaim records. Blank-claim rows are skipped."""
    df = pd.read_excel(xls, sheet_name=sheet_name)
    resolved = {field: _resolve(df, spellings) for field, spellings in colmap.items()}

    if resolved.get("entity") is None or resolved.get("claim") is None:
        raise ValueError(
            f"Sheet {sheet_name!r}: could not find required 'entity'/'claim' columns. "
            f"Found columns: {list(df.columns)}"
        )

    is_list = question in _LIST_QUESTIONS
    claims: list[GTClaim] = []
    auto_idx = 0
    for _, row in df.iterrows():
        entity = _clean_str(row.get(resolved["entity"]))
        claim_text = _clean_str(row.get(resolved["claim"]))
        if not entity or not claim_text:
            continue  # blank starter rows are ignored (see template Instructions)

        auto_idx += 1
        claim_id = ""
        if resolved.get("claim_id"):
            claim_id = _clean_str(row.get(resolved["claim_id"]))
        if not claim_id:
            claim_id = f"{_entity_slug(entity)}-{id_prefix}-{auto_idx:02d}"

        quote_id = None
        if resolved.get("quote_id"):
            quote_id = _clean_str(row.get(resolved["quote_id"])) or None

        quote = _clean_str(row.get(resolved["verbatim_quote"])) if resolved.get("verbatim_quote") else ""
        source_url = _clean_str(row.get(resolved["source_url"])) if resolved.get("source_url") else ""

        claims.append(GTClaim(
            entity=entity,
            entity_norm=normalise_entity(entity),
            question=question,
            claim_id=claim_id,
            quote_id=quote_id,
            claim=claim_text,
            verbatim_quote=quote,
            source_url=source_url,
            is_null=_is_null_sentinel(_normalise_value(claim_text)),
            is_list=is_list,
            type=_clean_str(row.get(resolved["type"])) if resolved.get("type") else "",
            dimension=_clean_str(row.get(resolved["dimension"])) if resolved.get("dimension") else "",
            flag=_clean_str(row.get(resolved["flag"])) if resolved.get("flag") else "",
            notes=_clean_str(row.get(resolved["notes"])) if resolved.get("notes") else "",
        ))
    return claims


def _norm_header(value) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _detect_header_row(raw: pd.DataFrame, required: set[str], max_scan: int = 15) -> int | None:
    """Find the row index whose cells contain all `required` header names.

    The real Excluded sheet opens with a title + legend, so the header is not on
    row 0; a naive read yields 'Unnamed:' columns. We scan the first `max_scan`
    rows for the genuine header row and return its 0-indexed position.
    """
    for idx in range(min(max_scan, len(raw))):
        cells = {_norm_header(v) for v in raw.iloc[idx].tolist()}
        if required <= cells:
            return idx
    return None


def _read_excluded(xls: pd.ExcelFile, sheet_name: str) -> list[ExcludedItem]:
    """Parse the Excluded audit tab, auto-detecting its real (non-first-row) header."""
    raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
    header_idx = _detect_header_row(raw, required={"category", "excluded_text"})
    if header_idx is None:
        raise ValueError(
            f"Excluded sheet {sheet_name!r}: could not locate a header row containing "
            f"'category' and 'excluded_text' in the first rows. First rows seen:\n"
            f"{raw.head(5).to_string(index=False, header=False)}"
        )

    df = pd.read_excel(xls, sheet_name=sheet_name, header=header_idx)
    resolved = {field: _resolve(df, spellings) for field, spellings in _EXCLUDED_COLS.items()}
    if resolved.get("category") is None or resolved.get("excluded_text") is None:
        raise ValueError(
            f"Excluded sheet {sheet_name!r}: missing 'category'/'excluded_text' after "
            f"header detection (row {header_idx}). Found columns: {list(df.columns)}"
        )

    items: list[ExcludedItem] = []
    for _, row in df.iterrows():
        text = _clean_str(row.get(resolved["excluded_text"]))
        category = _clean_str(row.get(resolved["category"])).lower().replace(" ", "_").replace("-", "_")
        if not text and not category:
            continue
        if category not in _EXCLUDED_CATEGORIES:
            raise ValueError(
                f"Excluded sheet: unrecognised category {category!r} for text {text!r}. "
                f"Expected one of {sorted(_EXCLUDED_CATEGORIES)}."
            )
        entity = _clean_str(row.get(resolved["entity"])) if resolved.get("entity") else ""
        items.append(ExcludedItem(
            entity=entity,
            entity_norm=normalise_entity(entity),
            excluded_text=text,
            excluded_text_norm=_normalise_value(text),
            category=category,
            rationale=_clean_str(row.get(resolved["rationale"])) if resolved.get("rationale") else "",
            scoring_treatment=_clean_str(row.get(resolved["scoring_treatment"])) if resolved.get("scoring_treatment") else "",
        ))
    return items


def read_ground_truth(filepath: str) -> GroundTruth:
    """Parse the analyst ground-truth workbook into a GroundTruth object.

    The active GT set is the question-sheet rows as-is (no Excluded subtraction).
    """
    xls = pd.ExcelFile(filepath)
    try:
        sheet_plan = [
            (Q_SUSTAINABILITY, _SUSTAINABILITY_COLS, "SUS"),
            (Q_MILK_TYPES, _MILK_COLS, "MILK"),
            (Q_PARENT_COMPANY, _PARENT_COLS, "PAR"),
        ]
        active_claims: list[GTClaim] = []
        for question, colmap, prefix in sheet_plan:
            sheet = _find_sheet(xls, question)
            if sheet is None:
                raise ValueError(
                    f"Ground-truth workbook missing a {question!r} sheet. "
                    f"Found sheets: {xls.sheet_names}"
                )
            active_claims.extend(_read_question_sheet(xls, sheet, question, colmap, prefix))

        excluded_sheet = _find_sheet(xls, "Excluded")
        excluded = _read_excluded(xls, excluded_sheet) if excluded_sheet else []

        flags_sheet = _find_sheet(xls, "Flags")
        flags: list[dict] = []
        if flags_sheet:
            flags = pd.read_excel(xls, sheet_name=flags_sheet).fillna("").to_dict("records")
    finally:
        xls.close()

    quote_id_groups: dict[str, list[str]] = {}
    for c in active_claims:
        if c.quote_id:
            quote_id_groups.setdefault(c.quote_id, []).append(c.claim_id)

    return GroundTruth(
        active_claims=active_claims,
        excluded=excluded,
        quote_id_groups=quote_id_groups,
        flags=flags,
    )


# --- self-check (verification item 1) ----------------------------------------
def _selfcheck(filepath: str) -> None:
    gt = read_ground_truth(filepath)

    print(f"\n=== gt_reader self-check: {filepath} ===\n")
    print(f"entities: {len(gt.entities())} | active GT claims (recall denominator): "
          f"{len(gt.active_claims)}  [question sheets as-is, no Excluded subtraction]\n")

    print("--- active GT set per (entity, question) ---")
    questions = [Q_SUSTAINABILITY, Q_MILK_TYPES, Q_PARENT_COMPANY]
    for entity_norm in sorted({c.entity_norm for c in gt.active_claims}):
        label = next(c.entity for c in gt.active_claims if c.entity_norm == entity_norm)
        for q in questions:
            print(f"  {label} / {q}: {len(gt.active_for(entity_norm, q))} active claims")

    print("\n--- per-question totals (sanity vs analyst counts) ---")
    for q in questions:
        print(f"  {q}: {sum(1 for c in gt.active_claims if c.question == q)} claims")

    print("\n--- Excluded tab (precision-side only; header auto-detected) ---")
    by_cat = gt.excluded_by_category()
    print(f"  total excluded rows loaded: {len(gt.excluded)}")
    for cat in ("inclusion_bar", "dynamic_counter", "duplicate"):
        items = by_cat.get(cat, [])
        print(f"  [{cat}] {len(items)} item(s)")
        for it in items[:2]:
            treat = f" | scoring_treatment={it.scoring_treatment!r}" if it.scoring_treatment else ""
            print(f"      - {it.excluded_text!r} (entity={it.entity!r}){treat}")

    print("\n--- confirm Excluded did NOT shrink the GT denominator ---")
    excluded_texts = {it.excluded_text_norm for it in gt.excluded}
    leaked = [
        c.claim_id for c in gt.active_claims
        if _normalise_value(c.claim) in excluded_texts
    ]
    print(f"  active claims whose text also appears on the Excluded tab: {len(leaked)} "
          f"(expected 0 — sheets are pre-cleaned){' -> ' + ', '.join(leaked) if leaked else ''}")

    print(f"\nquote_id groups (merged-claim recall handling): {len(gt.quote_id_groups)} groups, "
          f"{sum(1 for v in gt.quote_id_groups.values() if len(v) > 1)} span >1 claim\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python src/eval/gt_reader.py <ground_truth.xlsx>")
        sys.exit(2)
    _selfcheck(sys.argv[1])
