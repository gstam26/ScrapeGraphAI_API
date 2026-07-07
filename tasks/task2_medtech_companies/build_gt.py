"""
Ground-truth builder — Task 2: Medical Device Companies

GT format used by diagnostics/generic_eval.py:
  Sheet "GroundTruth": entity | question | value | is_list | verbatim_quote | source_url | notes
  Sheet "Metadata":    key | value

Verification notes (training knowledge + web spot-checks, 2026-07-07):
  Medtronic     : founded 1949 Earl Bakken & Palmer Hermundslie, Minneapolis MN;
                  HQ moved to Dublin Ireland 2015 after Covidien acquisition.
  Stryker       : founded 1941 by Dr. Homer Stryker, Kalamazoo MI.
  Boston Sci.   : founded 1979 John Abele & Pete Nicholas, Marlborough MA;
                  CEO Michael F. Mahoney web-confirmed.
  Edwards       : founded 1999 as spin-off from Baxter (Miles Edwards had prior co 1958),
                  Irvine CA; CEO Bernard Zovighian (appointed 2024).
  Abbott        : founded 1888 Wallace Calvin Abbott, North Chicago IL;
                  CEO Robert Ford (since 2020).
  Zimmer Biomet : Zimmer founded 1927 Warsaw IN; merged with Biomet 2015;
                  CEO Ivan Tornos (since 2022).

Q3 GT (product areas): taken from publicly listed business segment names.
  Pipeline may extract more granular sub-areas — these are GT-gap, not hallucinations.

Q4 GT (CEO): verified 2026-07-07. Flag as time-sensitive; re-verify before
  reusing this GT more than 6 months after creation date.

Run:
    python tasks/task2_medtech_companies/build_gt.py
"""
import os
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "ground_truth.xlsx")

GT_ROWS = [
    # ── Medtronic ──────────────────────────────────────────────────────────────
    ("Medtronic", "Global headquarters", "Dublin, Ireland", False,
     "", "https://www.medtronic.com/", "Legal HQ since 2015 Covidien merger"),

    ("Medtronic", "Year founded", "1949", False,
     "", "https://www.medtronic.com/", ""),

    ("Medtronic", "Main product areas or therapy areas", "Cardiovascular",    True, "", "https://www.medtronic.com/", ""),
    ("Medtronic", "Main product areas or therapy areas", "Neuroscience",       True, "", "https://www.medtronic.com/", ""),
    ("Medtronic", "Main product areas or therapy areas", "Medical Surgical",   True, "", "https://www.medtronic.com/", ""),
    ("Medtronic", "Main product areas or therapy areas", "Diabetes",           True, "", "https://www.medtronic.com/", ""),

    ("Medtronic", "Current CEO", "Geoff Martha", False,
     "", "https://www.medtronic.com/", "CEO since 2020; verify if >6 months after GT date"),

    # ── Stryker ────────────────────────────────────────────────────────────────
    ("Stryker", "Global headquarters", "Kalamazoo, Michigan, USA", False,
     "", "https://www.stryker.com/", ""),

    ("Stryker", "Year founded", "1941", False,
     "", "https://www.stryker.com/", "Dr. Homer Stryker"),

    ("Stryker", "Main product areas or therapy areas", "MedSurg",         True, "", "https://www.stryker.com/", ""),
    ("Stryker", "Main product areas or therapy areas", "Neurotechnology", True, "", "https://www.stryker.com/", ""),
    ("Stryker", "Main product areas or therapy areas", "Orthopaedics",    True, "", "https://www.stryker.com/", ""),

    ("Stryker", "Current CEO", "Kevin Lobo", False,
     "", "https://www.stryker.com/", "CEO since 2012; verify if >6 months after GT date"),

    # ── Boston Scientific ──────────────────────────────────────────────────────
    ("Boston Scientific", "Global headquarters", "Marlborough, Massachusetts, USA", False,
     "", "https://www.bostonscientific.com/en-US/about-us.html", ""),

    ("Boston Scientific", "Year founded", "1979", False,
     "", "https://www.bostonscientific.com/en-US/about-us.html", "John Abele & Pete Nicholas"),

    ("Boston Scientific", "Main product areas or therapy areas", "Cardiology",         True, "", "https://www.bostonscientific.com/en-US/about-us.html", ""),
    ("Boston Scientific", "Main product areas or therapy areas", "Electrophysiology",  True, "", "https://www.bostonscientific.com/en-US/about-us.html", ""),
    ("Boston Scientific", "Main product areas or therapy areas", "Gastroenterology",   True, "", "https://www.bostonscientific.com/en-US/about-us.html", ""),
    ("Boston Scientific", "Main product areas or therapy areas", "Urology",            True, "", "https://www.bostonscientific.com/en-US/about-us.html", ""),
    ("Boston Scientific", "Main product areas or therapy areas", "Oncology",           True, "", "https://www.bostonscientific.com/en-US/about-us.html", ""),
    ("Boston Scientific", "Main product areas or therapy areas", "Pulmonology",        True, "", "https://www.bostonscientific.com/en-US/about-us.html", ""),
    ("Boston Scientific", "Main product areas or therapy areas", "Neuroscience",       True, "", "https://www.bostonscientific.com/en-US/about-us.html", ""),

    ("Boston Scientific", "Current CEO", "Michael F. Mahoney", False,
     "Boston Scientific CEO Michael F. Mahoney",
     "https://www.bostonscientific.com/en-US/about-us.html",
     "Web-verified 2026-07-07"),

    # ── Edwards Lifesciences ───────────────────────────────────────────────────
    ("Edwards Lifesciences", "Global headquarters", "Irvine, California, USA", False,
     "", "https://www.edwards.com/", ""),

    ("Edwards Lifesciences", "Year founded", "1999", False,
     "", "https://www.edwards.com/",
     "Spun out from Baxter International 1999; Miles Edwards' original co. 1958"),

    ("Edwards Lifesciences", "Main product areas or therapy areas", "Transcatheter Heart Valve", True, "", "https://www.edwards.com/", ""),
    ("Edwards Lifesciences", "Main product areas or therapy areas", "Surgical Structural Heart",  True, "", "https://www.edwards.com/", ""),
    ("Edwards Lifesciences", "Main product areas or therapy areas", "Critical Care",              True, "", "https://www.edwards.com/", ""),

    ("Edwards Lifesciences", "Current CEO", "Bernard Zovighian", False,
     "", "https://www.edwards.com/",
     "CEO since 2024; succeeded Michael Mussallem. Verify if >6 months after GT date"),

    # ── Abbott ────────────────────────────────────────────────────────────────
    ("Abbott", "Global headquarters", "North Chicago, Illinois, USA", False,
     "", "https://www.abbott.com/", ""),

    ("Abbott", "Year founded", "1888", False,
     "", "https://www.abbott.com/", "Founded by Wallace Calvin Abbott"),

    ("Abbott", "Main product areas or therapy areas", "Diagnostics",            True, "", "https://www.abbott.com/", ""),
    ("Abbott", "Main product areas or therapy areas", "Medical Devices",        True, "", "https://www.abbott.com/", ""),
    ("Abbott", "Main product areas or therapy areas", "Nutrition",              True, "", "https://www.abbott.com/", ""),
    ("Abbott", "Main product areas or therapy areas", "Established Pharmaceuticals", True, "", "https://www.abbott.com/", ""),

    ("Abbott", "Current CEO", "Robert Ford", False,
     "", "https://www.abbott.com/",
     "CEO since 2020; verify if >6 months after GT date"),

    # ── Zimmer Biomet ─────────────────────────────────────────────────────────
    ("Zimmer Biomet", "Global headquarters", "Warsaw, Indiana, USA", False,
     "", "https://www.zimmerbiomet.com/", ""),

    ("Zimmer Biomet", "Year founded", "1927", False,
     "", "https://www.zimmerbiomet.com/",
     "Zimmer Manufacturing founded 1927; merged with Biomet 2015"),

    ("Zimmer Biomet", "Main product areas or therapy areas", "Knee",       True, "", "https://www.zimmerbiomet.com/", ""),
    ("Zimmer Biomet", "Main product areas or therapy areas", "Hip",        True, "", "https://www.zimmerbiomet.com/", ""),
    ("Zimmer Biomet", "Main product areas or therapy areas", "Shoulder",   True, "", "https://www.zimmerbiomet.com/", ""),
    ("Zimmer Biomet", "Main product areas or therapy areas", "Spine",      True, "", "https://www.zimmerbiomet.com/", ""),
    ("Zimmer Biomet", "Main product areas or therapy areas", "Dental",     True, "", "https://www.zimmerbiomet.com/", ""),
    ("Zimmer Biomet", "Main product areas or therapy areas", "Trauma",     True, "", "https://www.zimmerbiomet.com/", ""),

    ("Zimmer Biomet", "Current CEO", "Ivan Tornos", False,
     "", "https://www.zimmerbiomet.com/",
     "CEO since 2022; verify if >6 months after GT date"),
]

gt_df = pd.DataFrame(GT_ROWS, columns=[
    "entity", "question", "value", "is_list",
    "verbatim_quote", "source_url", "notes",
])

meta_df = pd.DataFrame([
    ("task_name",    "Task 2 — Medical Device Companies"),
    ("difficulty",   "Medium — depth 1, crawl required for product/leadership pages"),
    ("entities",     "6"),
    ("questions",    "4 (Global headquarters, Year founded, Main product areas, Current CEO)"),
    ("depth",        "1"),
    ("created",      "2026-07-07"),
    ("gt_verified",  "Training knowledge + web spot-checks 2026-07-07. "
                     "Boston Scientific CEO web-verified. "
                     "CEO rows are time-sensitive — re-verify before reuse."),
    ("eval_script",  "python diagnostics/generic_eval.py "
                     "tasks/task2_medtech_companies/ground_truth.xlsx <pipeline_output.xlsx>"),
    ("notes",        "Q3 segment names are official division names; pipeline may extract "
                     "sub-divisions — GT-gap not hallucination. "
                     "Q4 (CEO) stamped 2026-07-07; refresh before reusing after ~6 months."),
], columns=["key", "value"])

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    gt_df.to_excel(w,   sheet_name="GroundTruth", index=False)
    meta_df.to_excel(w, sheet_name="Metadata",    index=False)

print(f"Wrote {OUT}  ({len(gt_df)} GT rows across {gt_df['entity'].nunique()} entities)")
