"""Build the CMO v3 instructions arm: baseline page set pinned, Y/N capability
instructions tightened to the analyst's revealed disclosure norm.

Motivation (decision-log 2026-07-29, NLI join): the v2 Y/N guidance already
said "if torn, Not disclosed", but the GT scoring showed the tool still (a)
asserted Yes from bare menu labels — the guidance sets no evidence bar for
Yes — and (b) read specialization wording as a No-implication ("focuses
exclusively on plastic injection moulding" -> PCB No) where the analyst wrote
Not disclosed. v3 changes ONLY the shared Y/N instruction, making both rules
explicit; the 8 prose/numeric questions keep their v2 instructions verbatim
so the A/B stays narrow. Caitlin's GT template is untouched — v3 aligns the
tool with the analyst's revealed interpretation of the same contract.

The output is a REPLAY workbook (standing 2026-07-06 requirement): pages are
pinned to the baseline run's Acquire Log at depth 0, so the arm differs from
the baseline in instructions only — no crawl/page-set drift. The extract
cache key includes instruction text (2026-07-23), so v3 re-extracts even
with the baseline's caches intact while fetches stay cache-served.

Defaults build the 5-GT-entity slice (Adapt/Arrk/Asteelflash/Automatic/
Avenue — the only scoreable cohort today); --all keeps all baseline
entities. Output is gitignored client data — regenerate per machine.

Usage (work laptop):
    python scripts/build_cmo_input_v3.py \
        --baseline outputs/cmo_output_v2_FULL_baseline_2026-07-24.xlsx
Then: python main.py -> cmo-inputs/cmo_input_v3_replay.xlsx
"""
import argparse
import os
import sys

import pandas as pd

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from diagnostics.build_replay_input import build_replay_input
from src.io_excel import _parse_entity_list

IN_DIR = os.path.join(_REPO, "cmo-inputs")
DEFAULT_OUT = os.path.join(IN_DIR, "cmo_input_v3_replay.xlsx")

GT_ENTITIES = [
    "Adapt EMS",
    "Arrk Group",
    "Asteelflash",
    "Automatic Manufacturing Ltd",
    "Avenue Mould",
]

# v2's shared Y/N guidance (build_cmo_gt_sheet.YN) — matched EXACTLY so the
# patch fails loudly if the v2 spec ever drifts instead of silently
# half-applying.
YN_V2 = ("Yes / No / Not disclosed. No = the site says or clearly implies they "
         "don't; Not disclosed = the site is silent. If torn, Not disclosed.")

# v3: same norm, made specific. Two additions, pre-registered against the two
# measured error mechanisms: an evidence bar for Yes (menu-label class) and
# the facts-vs-focus line for implied No (specialization class).
YN_V3 = ("Yes / No / Not disclosed. Yes needs explicit evidence: the website "
         "describes or demonstrates the capability (a service description, "
         "facility, or stated offering) — a bare menu label, category name, "
         "or generic marketing phrase is not enough. No = the site says or "
         "clearly implies they don't from stated facts (e.g. manufacturing "
         "listed only in the US -> 'exclusively in China?' = No); if the "
         "implication rests only on the company's stated focus or "
         "specialization, answer Not disclosed. If torn, Not disclosed.")


def main() -> int:
    ap = argparse.ArgumentParser(description="build the CMO v3 instructions arm")
    ap.add_argument("--baseline", required=True,
                    help="frozen baseline output .xlsx (with Acquire Log)")
    ap.add_argument("--input", default=os.path.join(IN_DIR, "cmo_input_v2.xlsx"))
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--all", action="store_true",
                    help="keep all baseline entities (default: the 5 GT rows)")
    args = ap.parse_args()

    # gate_failed pages are kept: since the full-page rescue (2026-07-23,
    # 6cf1d36) gate failure is diagnostic, not a drop — the baseline extracted
    # those pages, so the arm must replay them or the A/B confounds the
    # instruction change with a page-set difference.
    build_replay_input(args.baseline, args.input, args.out,
                       keep_statuses=("ok", "cached", "gate_failed"))

    wanted = None if args.all else set(GT_ENTITIES)
    x = pd.ExcelFile(args.out)
    sheets = {name: pd.read_excel(x, name) for name in x.sheet_names}
    x.close()

    q = sheets["questions"]
    is_v2_yn = q["instructions"].astype(str).str.strip() == YN_V2
    if is_v2_yn.sum() != 9:
        raise SystemExit(
            f"Expected exactly 9 questions carrying the v2 Y/N guidance, found "
            f"{is_v2_yn.sum()} — the v2 spec has drifted; update YN_V2 here "
            "before building the arm.")
    q.loc[is_v2_yn, "instructions"] = YN_V3
    print(f"Patched Y/N instruction on {is_v2_yn.sum()} questions:")
    for text in q.loc[is_v2_yn, "question"]:
        print(f"  - {text}")

    if wanted is not None:
        ents = sheets["entities"]
        before_e = len(ents)
        sheets["entities"] = ents[ents["entity"].astype(str).isin(wanted)]
        missing = wanted - set(sheets["entities"]["entity"].astype(str))
        if missing:
            raise SystemExit(f"GT entities not in baseline input: {sorted(missing)}")
        urls = sheets["urls"]
        keep = urls["entities"].astype(str).map(
            lambda cell: any(e in wanted for e in _parse_entity_list(cell)))
        sheets["urls"] = urls[keep]
        print(f"Entity slice: {before_e} -> {len(sheets['entities'])} entities, "
              f"{len(urls)} -> {len(sheets['urls'])} pinned pages")

    with pd.ExcelWriter(args.out, engine="openpyxl") as w:
        for name in ("entities", "urls", "questions", "config"):
            sheets[name].to_excel(w, sheet_name=name, index=False)
    print(f"v3 arm written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
