"""Analyst-fill convention check — run BEFORE gt_convert, especially on a
second analyst's file.

gt_convert.py is deterministic but convention-sensitive: it decides
null-vs-blank, item splitting and is_list from the CELL TEXT alone. Two
analysts using different fill conventions therefore produce GTs that score
the same pipeline differently — most dangerously on the blank ("not
assessed", cell skipped) vs null-marker ("confirmed absent", pipeline
assertion = FP) boundary, which moves precision directly.

This tool profiles one file, or diffs two, using gt_convert's OWN functions
(split_cell, _is_null_marker, read_matrix) so what it reports is what the
converter will actually do — not a re-implementation. Read-only; prints
findings; never writes.

Checks per question column:
  * blank vs recognised-null-marker usage (the precision-moving boundary)
  * NEAR-null values ("Unknown", "not found", "?") that are NOT in
    _NULL_MARKERS — these convert to real GT answers and then score the
    pipeline's null as a miss AND its assertion as wrong
  * navigation notes ("Same as above", "Website failed to load") — the class
    manually blanked during the staged conversion of earlier fills; they
    convert to fake GT answers if left in
  * multi-item cells, semicolon/comma habits, bullets
  * the is_list verdict the converter would infer — and, with two files,
    columns where the two fills would flip it (converting merged vs separate
    files then yields different grains for the SAME rows)

Usage (pass the SAME flags you will pass to gt_convert, so the profile
matches the real conversion):
  python scripts/gt_convention_check.py analyst_B.xlsx --sheet "Ground Truth" \
      --ignore-cols "Website,Supporting quotes / sources" --single "Q_desc,Q_vol"
  python scripts/gt_convention_check.py analyst_A.xlsx analyst_B.xlsx [same flags]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.eval.gt_convert import (  # noqa: E402 — the converter's own rules
    _cell_str, _is_null_marker, _norm, read_matrix, split_cell,
)

# Values an analyst plausibly MEANS as "absent/unassessed" that gt_convert
# does NOT recognise (deliberately short marker list there). Any hit here
# becomes a real GT value on conversion — flag, don't guess.
_NEAR_NULL_RE = re.compile(
    r"^(unknown|unclear|not\s+(found|stated|specified|available|mentioned|"
    r"listed|provided|given|sure)|no\s+info(rmation)?(\s+(found|available|"
    r"provided))?|nothing(\s+found)?|tbd|t\.b\.d\.?|\?+|x|nil|blank|missing|"
    r"unavailable)$",
    re.IGNORECASE,
)

# Analyst navigation notes observed in earlier analyst-filled templates
# (blanked to not-assessed during that conversion, by hand).
_NAV_NOTE_RE = re.compile(
    r"(same as (above|entry)|see (entr|above|row)|website (failed|did ?n.t "
    r"load|down|not work)|could ?n.t (access|load|find|open)|unable to "
    r"(access|load|find|open)|broken link|page not found|404)",
    re.IGNORECASE,
)


def profile(path: str, sheet: str | None, entity_col: str | None,
            comma_cols: set[str], force_single: set[str],
            ignore_cols: set[str]) -> dict:
    df = read_matrix(path, sheet)
    columns = [str(c).strip() for c in df.columns]
    ent_col = entity_col or columns[0]
    if ent_col not in columns:
        raise SystemExit(f"{path}: entity column {ent_col!r} not found. "
                         f"Columns: {columns}")
    questions = [c for c in columns
                 if c != ent_col and _norm(c) not in ignore_cols]

    stats: dict[str, dict] = {q: {
        "filled": 0, "blank": 0, "null_marker": 0,
        "near_null": [], "nav_note": [], "multiline": 0, "semicolon": 0,
        "comma_unsplit": 0, "bullet": 0, "multi_item_entities": [],
    } for q in questions}

    entities: list[str] = []
    continuation_rows = 0
    leading_skipped = 0
    current_entity = ""
    for _, row in df.iterrows():
        name = _cell_str(row[ent_col])
        if name:
            current_entity = name
            entities.append(name)
        elif current_entity:
            # blank entity cell mid-file: gt_convert CONTINUES the previous
            # entity (merged-cell convention). Only a finding if any question
            # cell on the row is filled.
            if any(_cell_str(row[q]) for q in questions):
                continuation_rows += 1
        else:
            leading_skipped += 1
            continue
        for q in questions:
            raw = _cell_str(row[q])
            s = stats[q]
            if not raw:
                s["blank"] += 1
                continue
            s["filled"] += 1
            qn = _norm(q)
            single = qn in force_single
            items = split_cell(raw, comma=qn in comma_cols)
            real_items = [i for i in items if not _is_null_marker(i)]
            if _is_null_marker(raw) or (len(items) == 1 and not real_items):
                s["null_marker"] += 1
            elif _NEAR_NULL_RE.match(raw.strip()):
                s["near_null"].append((current_entity, raw.strip()))
            if _NAV_NOTE_RE.search(raw):
                s["nav_note"].append((current_entity, raw.strip()[:60]))
            if "\n" in raw:
                s["multiline"] += 1
            if ";" in raw:
                s["semicolon"] += 1
            if not single and qn not in comma_cols and any(
                    "," in i for i in items):
                s["comma_unsplit"] += 1
            if re.match(r"^\s*(?:[-*•–]|\d{1,3}[.)])\s+", raw):
                s["bullet"] += 1
            if not single and len(real_items) > 1:
                s["multi_item_entities"].append(current_entity)

    dupes = sorted({e for e in entities if entities.count(e) > 1})
    for q in questions:
        qn = _norm(q)
        stats[q]["would_infer_list"] = (
            False if qn in force_single
            else bool(stats[q]["multi_item_entities"]))
    return {"path": path, "questions": questions, "stats": stats,
            "entities": entities, "dupes": dupes,
            "continuation_rows": continuation_rows,
            "leading_skipped": leading_skipped,
            "force_single": force_single}


def print_profile(p: dict) -> None:
    print(f"\n=== {os.path.basename(p['path'])} — "
          f"{len(set(p['entities']))} entities, "
          f"{len(p['questions'])} question columns ===")
    if p["leading_skipped"]:
        print(f"  {p['leading_skipped']} leading row(s) before any entity "
              f"name skipped (guidance row etc.)")
    if p["continuation_rows"]:
        print(f"  {p['continuation_rows']} blank-entity row(s) with content "
              f"-> CONTINUE the previous entity (merged-cell convention); "
              f"verify that is what the analyst meant")
    if p["dupes"]:
        print(f"  !! duplicate entity names (rows will merge): {p['dupes']}")
    hdr = (f"  {'QUESTION':<42} {'fill':>4} {'blnk':>4} {'null':>4} "
           f"{'list?':>5}  flags")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for q in p["questions"]:
        s = p["stats"][q]
        flags = []
        if s["near_null"]:
            flags.append(f"NEAR-NULL x{len(s['near_null'])}")
        if s["nav_note"]:
            flags.append(f"NAV-NOTE x{len(s['nav_note'])}")
        if s["comma_unsplit"]:
            flags.append(f"commas-unsplit x{s['comma_unsplit']}")
        if s["bullet"]:
            flags.append(f"bullets x{s['bullet']}")
        listmark = ("single*" if _norm(q) in p["force_single"]
                    else ("LIST" if s["would_infer_list"] else "single"))
        print(f"  {q[:42]:<42} {s['filled']:>4} {s['blank']:>4} "
              f"{s['null_marker']:>4} {listmark:>6}  {', '.join(flags)}")
    for q in p["questions"]:
        s = p["stats"][q]
        for ent, v in s["near_null"]:
            print(f"    NEAR-NULL  [{q[:30]} / {ent}]: {v!r} -> would become "
                  f"a REAL GT value, not a null")
        for ent, v in s["nav_note"]:
            print(f"    NAV-NOTE   [{q[:30]} / {ent}]: {v!r} -> blank it "
                  f"before converting (07-31 precedent)")
    print("  (* forced by --single; 'list?' = what gt_convert would infer "
          "with these flags)")


def print_divergence(a: dict, b: dict) -> None:
    print(f"\n=== DIVERGENCE: {os.path.basename(a['path'])} vs "
          f"{os.path.basename(b['path'])} ===")
    qa, qb = set(a["questions"]), set(b["questions"])
    if qa - qb:
        print(f"  !! columns only in A: {sorted(qa - qb)}")
    if qb - qa:
        print(f"  !! columns only in B: {sorted(qb - qa)}")
    shared = [q for q in a["questions"] if q in qb]
    findings = 0
    for q in shared:
        sa, sb = a["stats"][q], b["stats"][q]
        if sa["would_infer_list"] != sb["would_infer_list"]:
            findings += 1
            print(f"  IS_LIST FLIP [{q[:40]}]: A infers "
                  f"{sa['would_infer_list']}, B infers "
                  f"{sb['would_infer_list']} -> converting merged vs separate "
                  f"changes grain for the SAME rows. Force with "
                  f"--list/--single before converting either.")
        a_null, b_null = sa["null_marker"], sb["null_marker"]
        a_blank, b_blank = sa["blank"], sb["blank"]
        if (a_null == 0) != (b_null == 0) and (a_blank or b_blank):
            findings += 1
            who_null = "A" if a_null else "B"
            who_blank = "B" if a_null else "A"
            print(f"  NULL-VS-BLANK [{q[:40]}]: {who_null} writes null "
                  f"markers ({max(a_null, b_null)}), {who_blank} leaves "
                  f"blanks only ({b_blank if a_null else a_blank}). On a "
                  f"covered entity a BLANK cell can only charge FP (pipeline "
                  f"assertions unmatched; TP impossible, abstentions "
                  f"suppressed), while a NULL cell lets an abstention score "
                  f"TP (null_match) and charges an assertion FN+FP. "
                  f"{who_blank}'s entities lose every null_match TP -> "
                  f"precision AND recall not comparable across their "
                  f"entities until resolved.")
        if sb["near_null"] and not sa["near_null"]:
            findings += 1
            print(f"  NEAR-NULL [{q[:40]}]: only B uses unrecognised "
                  f"absence wording ({[v for _, v in sb['near_null'][:3]]}) "
                  f"-> becomes real GT values only on B's entities.")
        if (sa["multiline"] > 0) != (sb["multiline"] > 0) and (
                sa["comma_unsplit"] or sb["comma_unsplit"]):
            findings += 1
            who = "A" if sa["multiline"] else "B"
            other = "B" if who == "A" else "A"
            print(f"  SPLIT HABIT [{q[:40]}]: {who} separates items with "
                  f"newlines, {other} has comma-bearing cells and no "
                  f"newlines -> {other}'s lists may convert as ONE item. "
                  f"Inspect; consider --comma-split for {other}'s file only.")
        if (sa["bullet"] == 0) != (sb["bullet"] == 0):
            print(f"  note [{q[:40]}]: bullet habit differs (A x"
                  f"{sa['bullet']}, B x{sb['bullet']}) — harmless, "
                  f"bullets are stripped.")
    ea, eb = set(a["entities"]), set(b["entities"])
    overlap = sorted(ea & eb)
    if overlap:
        print(f"  entity overlap ({len(overlap)}) — usable for "
              f"inter-annotator agreement: {overlap}")
    if not findings:
        print("  no precision-moving divergence detected by these checks "
              "(near-null/nav-note lists above still apply per file)")


def _csv_arg(text: str | None) -> set[str]:
    return ({_norm(t) for t in text.split(",") if t.strip()}
            if text else set())


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Profile analyst fill conventions before gt_convert; "
                    "diff two analysts' files.")
    ap.add_argument("file_a", help="Analyst matrix A (.xlsx/.csv)")
    ap.add_argument("file_b", nargs="?", default=None,
                    help="Optional analyst matrix B — enables divergence diff")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--entity-col", default=None)
    ap.add_argument("--comma-split", default=None, metavar="Q1,Q2")
    ap.add_argument("--single", default=None, metavar="Q1,Q2")
    ap.add_argument("--ignore-cols", default=None, metavar="C1,C2")
    args = ap.parse_args()

    kw = dict(sheet=args.sheet, entity_col=args.entity_col,
              comma_cols=_csv_arg(args.comma_split),
              force_single=_csv_arg(args.single),
              ignore_cols=_csv_arg(args.ignore_cols))
    a = profile(args.file_a, **kw)
    print_profile(a)
    if args.file_b:
        b = profile(args.file_b, **kw)
        print_profile(b)
        print_divergence(a, b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
