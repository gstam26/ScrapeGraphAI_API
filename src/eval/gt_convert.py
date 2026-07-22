"""
Analyst-matrix -> flat ground-truth converter.

Analysts naturally produce MATRIX-shaped answer tables (one row per entity,
one column per question, several items in a cell). The generic evaluator
(src/eval/generic_eval.py) reads a FLAT workbook (one row per expected claim).
This tool converts the former into the latter, so any analyst table plugs into
the evaluation without manual reshaping.

Input (Excel or CSV): first column = entity names (override: --entity-col),
every other column = one question. Example:

  Company   | R&D location            | Company type | Recent news
  Acme Dx   | Eden Prairie, MN        | own-product  | FDA clearance March 2026
            | Ballinasloe, Ireland    |              | Series B round
  Beta Labs | none                    | OEM          |

Conversion rules (all decisions printed, nothing silent):
  * Cells are split into one GT row per item on NEWLINES and SEMICOLONS.
    Commas are NOT split by default — they appear inside single items
    ("Eden Prairie, MN") more often than between items. Opt a column into
    comma-splitting with --comma-split "Question A,Question B".
  * Leading bullets/numbering ("- ", "* ", "1. ") are stripped per item.
  * is_list per question is INFERRED: any cell with 2+ items => list
    question. Override with --list / --single (comma-separated names).
    A --single column is never split: the whole cell is one answer.
  * Null markers ("none", "n/a", "-", "not disclosed", ...) become the
    canonical "None (not disclosed)" sentinel the evaluator scores as a
    true negative. EMPTY cells are skipped entirely — an empty cell means
    "analyst did not assess", never "analyst confirmed absence".
  * verbatim_quote / source_url stay empty (a matrix table doesn't carry
    them); the evaluator's quote signal simply doesn't fire, as designed.

The output is round-tripped through generic_eval.read_gt before writing is
reported, so a workbook this tool produces is one the evaluator accepts.

Usage:
  python src/eval/gt_convert.py analyst_matrix.xlsx --output ground_truth.xlsx
  python src/eval/gt_convert.py matrix.xlsx --output gt.xlsx --sheet "Answers"
  python src/eval/gt_convert.py matrix.xlsx --output gt.xlsx \
      --comma-split "Main projects" --single "Primary mission"
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Cell values (whole cell OR single split item, normalised) treated as the
# analyst confirming "this information is absent". Kept deliberately short:
# a marker list that grows starts swallowing real answers ("None of the
# above..."). All map to the canonical sentinel generic_eval scores against.
_NULL_MARKERS = {
    "none", "n/a", "na", "-", "--", "—", "not disclosed",
    "none (not disclosed)", "not disclosed on site", "no data", "no data found",
}
_CANONICAL_NULL = "None (not disclosed)"

# Leading list decoration stripped from each item: "- ", "* ", "• ", "1. ", "2) "
_BULLET_RE = re.compile(r"^\s*(?:[-*•–]|\d{1,3}[.)])\s+")


# ---------------------------------------------------------------------------
# Cell handling
# ---------------------------------------------------------------------------
def _cell_str(v) -> str:
    """Excel cell -> clean string. Integer-valued floats render without the
    trailing .0 pandas gives them ('2003', not '2003.0')."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _is_null_marker(text: str) -> bool:
    return _norm(text) in _NULL_MARKERS


def split_cell(text: str, comma: bool = False) -> list[str]:
    """Split one matrix cell into claim items.

    Newlines and semicolons always delimit; commas only when the column
    opted in. Bullets/numbering are stripped per item; empty fragments drop.
    """
    if not text.strip():
        return []
    pattern = r"[\n;,]" if comma else r"[\n;]"
    items = []
    for frag in re.split(pattern, text):
        frag = _BULLET_RE.sub("", frag.strip()).strip()
        if frag:
            items.append(frag)
    return items


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
def read_matrix(path: str, sheet: str | None = None) -> pd.DataFrame:
    """Read the analyst matrix with pandas' null-guessing DISABLED — 'None'
    and 'N/A' are meaningful analyst markers here, not missing data."""
    if path.lower().endswith(".csv"):
        return pd.read_csv(path, keep_default_na=False, na_values=[])
    xls = pd.ExcelFile(path)
    name = sheet or xls.sheet_names[0]
    if sheet and sheet not in xls.sheet_names:
        raise ValueError(f"Sheet {sheet!r} not found. Available: {xls.sheet_names}")
    return pd.read_excel(xls, sheet_name=name, keep_default_na=False, na_values=[])


def convert(
    df: pd.DataFrame,
    entity_col: str | None = None,
    comma_cols: set[str] | None = None,
    force_list: set[str] | None = None,
    force_single: set[str] | None = None,
    ignore_cols: set[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Matrix DataFrame -> (flat GT rows, human-readable decisions).

    Raises on flag names that match no question column — a typo must never
    silently leave a column on its default treatment.
    """
    comma_cols = {_norm(c) for c in (comma_cols or set())}
    force_list = {_norm(c) for c in (force_list or set())}
    force_single = {_norm(c) for c in (force_single or set())}
    ignore_cols = {_norm(c) for c in (ignore_cols or set())}

    columns = [str(c).strip() for c in df.columns]
    ent_col = entity_col or columns[0]
    if ent_col not in columns:
        raise ValueError(f"Entity column {ent_col!r} not found. Columns: {columns}")
    unknown_ignores = ignore_cols - {_norm(c) for c in columns}
    if unknown_ignores:
        raise ValueError(f"--ignore-cols names not found as columns: "
                         f"{sorted(unknown_ignores)}. Columns: {columns}")
    questions = [c for c in columns if c != ent_col and _norm(c) not in ignore_cols]
    if not questions:
        raise ValueError("Matrix has no question columns besides the entity column.")
    decisions_pre = [f"[{c}] column ignored (--ignore-cols)"
                     for c in columns if _norm(c) in ignore_cols]

    q_norms = {_norm(q) for q in questions}
    for flag_name, flagged in (("--comma-split", comma_cols),
                               ("--list", force_list),
                               ("--single", force_single)):
        unknown = flagged - q_norms
        if unknown:
            raise ValueError(f"{flag_name} names not found as question columns: "
                             f"{sorted(unknown)}. Questions: {questions}")
    both = force_list & force_single
    if both:
        raise ValueError(f"Questions in both --list and --single: {sorted(both)}")

    # Pass 1: split every cell (per-column rule) and collect items.
    # cells[(row_idx, q)] = list of items; entity rows with a blank entity
    # continue the PREVIOUS entity (analysts merge cells / leave repeats blank).
    cells: dict[tuple[str, str], list[str]] = {}
    entity_order: list[str] = []
    current_entity = ""
    for _, row in df.iterrows():
        name = _cell_str(row[ent_col])
        if name:
            current_entity = name
            if name not in entity_order:
                entity_order.append(name)
        if not current_entity:
            continue  # leading rows before any entity name
        for q in questions:
            qn = _norm(q)
            raw = _cell_str(row[q])
            if not raw:
                continue
            if qn in force_single:
                items = [" ; ".join(split_cell(raw))] if split_cell(raw) else []
            else:
                items = split_cell(raw, comma=qn in comma_cols)
            if items:
                cells.setdefault((current_entity, q), []).extend(items)

    # Pass 2: infer is_list per question (any cell with 2+ real items),
    # unless forced either way.
    decisions: list[str] = list(decisions_pre)
    is_list_by_q: dict[str, bool] = {}
    for q in questions:
        qn = _norm(q)
        if qn in force_list:
            is_list_by_q[q] = True
            decisions.append(f"[{q}] is_list=True (--list)")
            continue
        if qn in force_single:
            is_list_by_q[q] = False
            decisions.append(f"[{q}] is_list=False (--single; cells never split)")
            continue
        multi = [e for (e, qq), items in cells.items()
                 if qq == q and len([i for i in items if not _is_null_marker(i)]) > 1]
        is_list_by_q[q] = bool(multi)
        why = (f"inferred from multi-item cell(s): {', '.join(multi[:3])}"
               if multi else "no cell has 2+ items")
        decisions.append(f"[{q}] is_list={is_list_by_q[q]} ({why})")
        if qn in comma_cols:
            decisions.append(f"[{q}] comma-splitting ON (--comma-split)")

    # Pass 3: emit flat rows in stable (entity order, column order) order.
    rows: list[dict] = []
    for entity in entity_order:
        for q in questions:
            items = cells.get((entity, q), [])
            for item in items:
                value = _CANONICAL_NULL if _is_null_marker(item) else item
                rows.append({
                    "entity": entity,
                    "question": q,
                    "value": value,
                    # A null sentinel is a single-answer statement even in a
                    # list column ("nothing to list" is one fact, not a list).
                    "is_list": is_list_by_q[q] and value != _CANONICAL_NULL,
                    "verbatim_quote": "",
                    "source_url": "",
                    "notes": "",
                })

    return rows, decisions


def write_gt(rows: list[dict], out_path: str, source_path: str) -> None:
    gt_df = pd.DataFrame(
        rows,
        columns=["entity", "question", "value", "is_list",
                 "verbatim_quote", "source_url", "notes"],
    )
    meta_df = pd.DataFrame(
        [("converted_from", os.path.basename(source_path)),
         ("converted_on", date.today().isoformat()),
         ("converter", "src/eval/gt_convert.py"),
         ("eval_script", "python src/eval/generic_eval.py "
                         f"{os.path.basename(out_path)} <pipeline_output.xlsx>")],
        columns=["key", "value"],
    )
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        gt_df.to_excel(w, sheet_name="GroundTruth", index=False)
        meta_df.to_excel(w, sheet_name="Metadata", index=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _csv_arg(text: str | None) -> set[str]:
    return {t.strip() for t in text.split(",") if t.strip()} if text else set()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert a matrix-shaped analyst answer table into the flat "
                    "GT workbook generic_eval.py reads."
    )
    ap.add_argument("matrix", help="Analyst matrix (.xlsx or .csv): entity rows x question columns")
    ap.add_argument("--output", required=True, help="Path for the flat ground_truth.xlsx")
    ap.add_argument("--sheet", default=None, help="Sheet name (default: first sheet)")
    ap.add_argument("--entity-col", default=None,
                    help="Entity column header (default: first column)")
    ap.add_argument("--comma-split", default=None, metavar="Q1,Q2",
                    help="Question columns whose cells also split on commas")
    ap.add_argument("--list", dest="force_list", default=None, metavar="Q1,Q2",
                    help="Force these questions to is_list=True")
    ap.add_argument("--single", dest="force_single", default=None, metavar="Q1,Q2",
                    help="Force single-answer: never split these columns' cells")
    ap.add_argument("--ignore-cols", dest="ignore_cols", default=None, metavar="C1,C2",
                    help="Non-question columns to drop entirely (e.g. a prefilled "
                         "Website column, Notes, Date checked)")
    args = ap.parse_args()

    df = read_matrix(args.matrix, sheet=args.sheet)
    rows, decisions = convert(
        df,
        entity_col=args.entity_col,
        comma_cols=_csv_arg(args.comma_split),
        force_list=_csv_arg(args.force_list),
        force_single=_csv_arg(args.force_single),
        ignore_cols=_csv_arg(args.ignore_cols),
    )
    if not rows:
        sys.exit("No GT rows produced — is the matrix empty, or every cell blank?")

    write_gt(rows, args.output, args.matrix)

    # Round-trip check: the file we just wrote must parse with the evaluator's
    # own reader, or the conversion failed no matter how nice it looked.
    from src.eval.generic_eval import read_gt
    parsed = read_gt(args.output)
    if len(parsed) != len(rows):
        sys.exit(f"Round-trip mismatch: wrote {len(rows)} rows, "
                 f"generic_eval.read_gt read back {len(parsed)}.")

    print(f"Converted {args.matrix} -> {args.output}")
    print(f"  {len({r['entity'] for r in rows})} entities, "
          f"{len({r['question'] for r in rows})} questions, {len(rows)} GT rows "
          f"(round-trip verified)")
    print("  Decisions:")
    for d in decisions:
        print(f"    {d}")
    nulls = sum(1 for r in rows if r["value"] == _CANONICAL_NULL)
    if nulls:
        print(f"  {nulls} null-marker cell(s) -> \"{_CANONICAL_NULL}\"")


if __name__ == "__main__":
    main()
