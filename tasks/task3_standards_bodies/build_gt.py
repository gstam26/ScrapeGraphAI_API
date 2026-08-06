"""
Ground-truth builder — Task 3: International Standards Bodies

GT format used by src/eval/generic_eval.py:
  Sheet "GroundTruth": entity | question | value | is_list | verbatim_quote | source_url | notes
  Sheet "Metadata":    key | value

Verification notes (training knowledge + NIST web-verified 2026-07-07):
  ISO   : founded 1947, Geneva Switzerland, ~170 member bodies
  IEEE  : founded 1963, New York / Piscataway NJ, ~460 000 individual members
  IEC   : founded 1906, Geneva Switzerland, ~170 national committees
  NIST  : founded 1901, Gaithersburg MD (web-confirmed), no membership model
  BSI   : founded 1901, London UK, member count not prominently published
  DIN   : founded 1917, Berlin Germany, member count not prominently published

Q3 (Technical domains): representative list per organisation — not exhaustive.
  Pipeline will extract many more sub-domains; those that are real are GT-gap, not
  hallucinations. Expand GT after first run to include confirmed correct items.

Q4 (Member count) GT design:
  - ISO, IEEE, IEC: include approximate member counts as stated on their sites.
    Exact wording may vary year to year — accept close matches (e.g. "170 members"
    vs "167 member bodies") during manual review.
  - NIST: "None (not disclosed)" — confirmed: NIST is a US government agency with
    no membership model; member count is not applicable.
  - BSI, DIN: no Q4 row — GT does not claim knowledge; evaluate ai_only extractions
    for these two entities manually after running.  True hallucinations are numbers
    that are not on the website; correct non-disclosures score as true negatives.

Run:
    python tasks/task3_standards_bodies/build_gt.py
"""
import os
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "ground_truth.xlsx")

GT_ROWS = [
    # ── ISO ───────────────────────────────────────────────────────────────────
    ("ISO", "Headquarters location", "Geneva, Switzerland", False,
     "", "https://www.iso.org/home.html", ""),

    ("ISO", "Year established", "1947", False,
     "", "https://www.iso.org/home.html", ""),

    ("ISO", "Technical domains covered", "Manufactured goods",  True, "", "https://www.iso.org/home.html", ""),
    ("ISO", "Technical domains covered", "Technology",          True, "", "https://www.iso.org/home.html", ""),
    ("ISO", "Technical domains covered", "Food and agriculture",True, "", "https://www.iso.org/home.html", ""),
    ("ISO", "Technical domains covered", "Healthcare",          True, "", "https://www.iso.org/home.html", ""),
    ("ISO", "Technical domains covered", "Construction",        True, "", "https://www.iso.org/home.html", ""),
    ("ISO", "Technical domains covered", "Transport",           True, "", "https://www.iso.org/home.html", ""),
    ("ISO", "Technical domains covered", "Environment",         True, "", "https://www.iso.org/home.html", ""),
    ("ISO", "Technical domains covered", "Energy",              True, "", "https://www.iso.org/home.html", ""),

    ("ISO", "Member or participant count", "170 member bodies", False,
     "", "https://www.iso.org/home.html",
     "Approximate; exact number changes year to year. Accept close match in review."),

    # ── IEEE ──────────────────────────────────────────────────────────────────
    ("IEEE", "Headquarters location", "New York, USA", False,
     "", "https://www.ieee.org/about/", "Operations also in Piscataway NJ"),

    ("IEEE", "Year established", "1963", False,
     "", "https://www.ieee.org/about/",
     "Merger of AIEE (1884) and IRE (1912); IEEE as a body founded 1963"),

    ("IEEE", "Technical domains covered", "Electrical engineering",  True, "", "https://www.ieee.org/about/", ""),
    ("IEEE", "Technical domains covered", "Electronics",             True, "", "https://www.ieee.org/about/", ""),
    ("IEEE", "Technical domains covered", "Computer science",        True, "", "https://www.ieee.org/about/", ""),
    ("IEEE", "Technical domains covered", "Telecommunications",      True, "", "https://www.ieee.org/about/", ""),
    ("IEEE", "Technical domains covered", "Aerospace",               True, "", "https://www.ieee.org/about/", ""),
    ("IEEE", "Technical domains covered", "Energy",                  True, "", "https://www.ieee.org/about/", ""),
    ("IEEE", "Technical domains covered", "Biomedical engineering",  True, "", "https://www.ieee.org/about/", "May or may not be listed on about page"),

    ("IEEE", "Member or participant count", "460,000 members", False,
     "", "https://www.ieee.org/about/",
     "Individual members, not countries. Approximate; accept 400k–500k range in review."),

    # ── IEC ───────────────────────────────────────────────────────────────────
    ("IEC", "Headquarters location", "Geneva, Switzerland", False,
     "", "https://www.iec.ch/about", ""),

    ("IEC", "Year established", "1906", False,
     "", "https://www.iec.ch/about", ""),

    ("IEC", "Technical domains covered", "Electrical",         True, "", "https://www.iec.ch/about", ""),
    ("IEC", "Technical domains covered", "Electronic",         True, "", "https://www.iec.ch/about", ""),
    ("IEC", "Technical domains covered", "Electromagnetic",    True, "", "https://www.iec.ch/about",
     "Core IEC description; pipeline may also find energy, safety, IT etc."),

    ("IEC", "Member or participant count", "170 national committees", False,
     "", "https://www.iec.ch/about",
     "Approximate; accept 165–175 range in review"),

    # ── NIST ──────────────────────────────────────────────────────────────────
    ("NIST", "Headquarters location", "Gaithersburg, Maryland, USA", False,
     "100 Bureau Drive, Gaithersburg, MD",
     "https://www.nist.gov/about-nist", "Web-verified 2026-07-07"),

    ("NIST", "Year established", "1901", False,
     "The National Institute of Standards and Technology (NIST) was founded in 1901",
     "https://www.nist.gov/about-nist", "Web-verified 2026-07-07"),

    ("NIST", "Technical domains covered", "Measurement science",      True, "", "https://www.nist.gov/about-nist", ""),
    ("NIST", "Technical domains covered", "Cybersecurity",            True, "", "https://www.nist.gov/about-nist", ""),
    ("NIST", "Technical domains covered", "Artificial intelligence",  True, "", "https://www.nist.gov/about-nist", ""),
    ("NIST", "Technical domains covered", "Manufacturing",            True, "", "https://www.nist.gov/about-nist", ""),
    ("NIST", "Technical domains covered", "Quantum information science", True, "", "https://www.nist.gov/about-nist", ""),
    ("NIST", "Technical domains covered", "Bioscience",               True, "", "https://www.nist.gov/about-nist", ""),
    ("NIST", "Technical domains covered", "Communications",           True, "", "https://www.nist.gov/about-nist", ""),

    # NIST Q4 — no membership model: if pipeline extracts a number this is a hallucination
    ("NIST", "Member or participant count", "None (not disclosed)", False,
     "", "https://www.nist.gov/about-nist",
     "NIST is a US government agency; no membership. Q4 answer explicitly absent. "
     "Pipeline output of any number = hallucination."),

    # ── BSI Group ─────────────────────────────────────────────────────────────
    ("BSI Group", "Headquarters location", "London, United Kingdom", False,
     "", "https://www.bsigroup.com/", ""),

    ("BSI Group", "Year established", "1901", False,
     "", "https://www.bsigroup.com/",
     "Founded 1901 as Engineering Standards Committee, became BSI"),

    ("BSI Group", "Technical domains covered", "Construction",      True, "", "https://www.bsigroup.com/", ""),
    ("BSI Group", "Technical domains covered", "Healthcare",        True, "", "https://www.bsigroup.com/", ""),
    ("BSI Group", "Technical domains covered", "Engineering",       True, "", "https://www.bsigroup.com/", ""),
    ("BSI Group", "Technical domains covered", "IT and software",   True, "", "https://www.bsigroup.com/", ""),
    ("BSI Group", "Technical domains covered", "Consumer products", True, "", "https://www.bsigroup.com/", ""),
    ("BSI Group", "Technical domains covered", "Financial services",True, "", "https://www.bsigroup.com/", ""),
    ("BSI Group", "Technical domains covered", "Sustainability",    True, "", "https://www.bsigroup.com/", ""),

    # BSI Q4: no GT row — uncertain whether prominently stated; evaluate manually

    # ── DIN ───────────────────────────────────────────────────────────────────
    ("DIN", "Headquarters location", "Berlin, Germany", False,
     "", "https://www.din.de/en/about-din", ""),

    ("DIN", "Year established", "1917", False,
     "", "https://www.din.de/en/about-din", ""),

    ("DIN", "Technical domains covered", "Engineering",           True, "", "https://www.din.de/en/about-din", ""),
    ("DIN", "Technical domains covered", "Automotive",            True, "", "https://www.din.de/en/about-din", ""),
    ("DIN", "Technical domains covered", "Construction",          True, "", "https://www.din.de/en/about-din", ""),
    ("DIN", "Technical domains covered", "Chemicals",             True, "", "https://www.din.de/en/about-din", ""),
    ("DIN", "Technical domains covered", "Information technology",True, "", "https://www.din.de/en/about-din", ""),

    # DIN Q4: no GT row — uncertain whether prominently stated; evaluate manually
]

gt_df = pd.DataFrame(GT_ROWS, columns=[
    "entity", "question", "value", "is_list",
    "verbatim_quote", "source_url", "notes",
])

meta_df = pd.DataFrame([
    ("task_name",   "Task 3 — International Standards Bodies"),
    ("difficulty",  "Hard — depth 1, long-list domains, Q4 null risk for NIST, "
                    "heterogeneous site structures"),
    ("entities",    "6"),
    ("questions",   "4 (Headquarters location, Year established, "
                    "Technical domains covered, Member or participant count)"),
    ("depth",       "1"),
    ("created",     "2026-07-07"),
    ("gt_verified", "NIST: fully web-verified 2026-07-07. "
                    "ISO/IEEE/IEC/BSI/DIN: training knowledge; "
                    "founding years and HQ cities are stable; "
                    "domain lists are representative only."),
    ("eval_script", "python src/eval/generic_eval.py "
                    "tasks/task3_standards_bodies/ground_truth.xlsx <pipeline_output.xlsx>"),
    ("notes",
     "BSI and DIN have no Q4 GT row — pipeline Q4 output for these two must be reviewed "
     "manually post-run and then added to GT. "
     "NIST Q4 = 'None (not disclosed)': any numerical pipeline output is a hallucination. "
     "Q3 GT lists are conservative; expand after first run."),
], columns=["key", "value"])

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    gt_df.to_excel(w,   sheet_name="GroundTruth", index=False)
    meta_df.to_excel(w, sheet_name="Metadata",    index=False)

print(f"Wrote {OUT}  ({len(gt_df)} GT rows across {gt_df['entity'].nunique()} entities)")
