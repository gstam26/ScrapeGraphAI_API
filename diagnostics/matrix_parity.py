"""Matrix cell-population parity: a candidate run vs a pinned baseline run.

The fetch-backend ship bar (firecrawl-replacement.md, bar item 1) is NOT char
counts — Trafilatura strips nav text that Firecrawl markdown keeps, so char
ratios mislead. The question that matters is: of the Matrix cells the baseline
run populated, how many does the candidate run also populate? This tool
measures exactly that, per question, from the two output workbooks.

Standing requirement (decision-log 2026-07-06): comparative evals must replay
a PINNED page set — run the candidate on adlm-inputs/replay_validation_*.xlsx,
never a fresh crawl, or page-selection drift contaminates the comparison.
The laptop cache must also be moved aside for the candidate run: cache hits
bypass the fetch backend entirely, so a cache-served run measures nothing.

A cell counts as populated when it is non-empty and not "No data found";
conflict/unverified cells count as populated (the backend delivered content —
verification is a different layer's concern). Retention = populated in both /
populated in baseline. Pre-registered bar: >= 0.95 on Company type and
Diagnostics type (Q2/Q3).

Best-effort honesty check: if both workbooks carry a run-summary sheet with
acquire_tool_used / extract_tool_used, mismatched EXTRACT tools are flagged —
a parity diff with two moved variables (backend AND extractor) is not a
backend measurement.

Usage:
    python diagnostics/matrix_parity.py baseline.xlsx candidate.xlsx
    python diagnostics/matrix_parity.py adlm-outputs/replay_run_2026-07-06b.xlsx \
        outputs/replay_hybrid_2026-07-13.xlsx --bar 0.95
"""
import argparse
import sys

import pandas as pd

_EMPTY_MARKERS = {"", "no data found"}


def _populated(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    return str(value).strip().lower() not in _EMPTY_MARKERS


def _load_matrix(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Matrix")
    if "Entity" not in df.columns:
        sys.exit(f"{path}: Matrix sheet has no 'Entity' column")
    return df.set_index("Entity")


def _tools_used(path: str) -> tuple[str, str] | None:
    """Best-effort (acquire_tools, extract_tools) from a run-summary sheet."""
    try:
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            df = pd.read_excel(xl, sheet, nrows=0)
            if "acquire_tool_used" in df.columns and "extract_tool_used" in df.columns:
                full = pd.read_excel(xl, sheet)
                acq = sorted(set(full["acquire_tool_used"].dropna().astype(str)) - {""})
                ext = sorted(set(full["extract_tool_used"].dropna().astype(str)) - {""})
                return ", ".join(acq), ", ".join(ext)
    except Exception:
        pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Matrix populated-cell parity vs a baseline run")
    ap.add_argument("baseline", help="pinned baseline output workbook")
    ap.add_argument("candidate", help="candidate output workbook (same pinned page set)")
    ap.add_argument("--bar", type=float, default=0.95,
                    help="retention bar per question (default 0.95)")
    args = ap.parse_args()

    base = _load_matrix(args.baseline)
    cand = _load_matrix(args.candidate)

    base_tools, cand_tools = _tools_used(args.baseline), _tools_used(args.candidate)
    if base_tools and cand_tools:
        print(f"baseline : acquire=[{base_tools[0]}] extract=[{base_tools[1]}]")
        print(f"candidate: acquire=[{cand_tools[0]}] extract=[{cand_tools[1]}]")
        if base_tools[1] != cand_tools[1]:
            print("!! EXTRACT TOOL DIFFERS between runs — this diff conflates backend "
                  "and extractor; it is NOT a clean backend measurement.")
        print()

    only_base = sorted(set(base.index) - set(cand.index))
    only_cand = sorted(set(cand.index) - set(base.index))
    if only_base:
        print(f"!! entities only in baseline (excluded): {only_base}")
    if only_cand:
        print(f"!! entities only in candidate (excluded): {only_cand}")
    shared = [e for e in base.index if e in set(cand.index)]
    if not shared:
        sys.exit("No shared entities between the two Matrix sheets.")

    questions = [c for c in base.columns if c in set(cand.columns)]
    missing_q = [c for c in base.columns if c not in set(cand.columns)]
    if missing_q:
        print(f"!! question columns only in baseline (excluded): {missing_q}")

    print(f"{len(shared)} shared entities, {len(questions)} question columns, "
          f"bar {args.bar:.2f}\n")

    all_pass = True
    header = f"{'Question':30} {'base':>5} {'cand':>5} {'kept':>9} {'retention':>9}  verdict"
    print(header)
    print("-" * len(header))
    losses_by_q: dict[str, list[str]] = {}
    for q in questions:
        b_pop = {e for e in shared if _populated(base.at[e, q])}
        c_pop = {e for e in shared if _populated(cand.at[e, q])}
        kept = b_pop & c_pop
        retention = len(kept) / len(b_pop) if b_pop else 1.0
        ok = retention >= args.bar
        all_pass = all_pass and ok
        gains = len(c_pop - b_pop)
        note = f"PASS" if ok else "FAIL"
        if gains:
            note += f"  (+{gains} gained)"
        print(f"{q:30} {len(b_pop):>5} {len(c_pop):>5} "
              f"{f'{len(kept)}/{len(b_pop)}':>9} {retention:>8.1%}  {note}")
        lost = sorted(b_pop - c_pop)
        if lost:
            losses_by_q[q] = lost

    for q, lost in losses_by_q.items():
        print(f"\nLOST in '{q}' (populated in baseline, empty in candidate):")
        for e in lost:
            print(f"  - {e}")

    print(f"\nOverall: {'ALL QUESTIONS PASS' if all_pass else 'BELOW BAR'} at {args.bar:.2f}. "
          f"Pre-registered bar item 1 applies to Company type / Diagnostics type.")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
