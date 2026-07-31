"""One-off seed corrections from the analyst's 2026-07-31 GT notes.

The analyst flagged two seeds as pointing at the wrong site (her notes are in
the GT template's Website column, yellow-filled):

  OnCore Manufacturing Services : neotechcable.com is a CABLE company;
                                  the OnCore successor is neotech.com
  Providien                     : providienmedical.com -> amphenol-cmt.com
                                  (Amphenol CMT acquisition)

Flagged but NOT patched (no replacement known): Medifiq (mediq.co.uk is a
different company — Mediq pharmacy services), KMC Systems (hiarc.inc "does
not correlate with CMO name?"), Hans Koch (domain parked/for sale).

Run on each machine (the input workbooks are gitignored, so the patch must be
applied locally): edits cmo-inputs/cmo_input.xlsx in place, then regenerate
the derived workbooks:

    python scripts/patch_cmo_seeds_20260731.py
    python scripts/build_cmo_gt_sheet.py

Idempotent: re-running after the patch (or after the builder) is a no-op.
"""
import os
import sys

import openpyxl

SEED_FIXES = {
    "OnCore Manufacturing Services": (
        "https://www.neotechcable.com/", "https://www.neotech.com/"),
    "Providien": (
        "https://providienmedical.com/", "https://amphenol-cmt.com/"),
}

WB_PATH = os.path.join("cmo-inputs", "cmo_input.xlsx")


def main() -> int:
    if not os.path.exists(WB_PATH):
        sys.exit(f"Missing {WB_PATH} — this machine has no CMO input to patch.")
    wb = openpyxl.load_workbook(WB_PATH)
    ws = wb["urls"]
    changed = 0
    for row in ws.iter_rows(min_row=2):
        url_cell, _, ent_cell = row[0], row[1], row[2]
        ents = str(ent_cell.value or "")
        for entity, (old, new) in SEED_FIXES.items():
            if entity not in ents:
                continue
            current = str(url_cell.value or "").rstrip("/")
            if current == new.rstrip("/"):
                print(f"already patched: {entity} -> {new}")
            elif current == old.rstrip("/"):
                url_cell.value = new
                changed += 1
                print(f"PATCHED {entity}: {old} -> {new}")
            else:
                print(f"! {entity} seed is {current!r} (neither old nor new) — "
                      f"left untouched, check manually")
    if changed:
        wb.save(WB_PATH)
        print(f"{changed} seed(s) updated in {WB_PATH}. "
              f"Now run: python scripts/build_cmo_gt_sheet.py")
    else:
        print("No changes written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
