"""
Task 3 — International Standards Bodies  (HARD / Depth-1)

6 international technical standards organisations.  Harder than Task 2 because:
  - Q3 (technical domains) is a long list embedded across sector pages, not a
    neat product-division menu — tests filter routing and multi-page synthesis.
  - Q4 (member count) is only stated by some organisations (NIST has no members;
    BSI & DIN do not prominently publish counts) — tests null / not-disclosed handling.
  - Sites vary widely: government portal (NIST), European membership body (IEC),
    commercial standards publisher (BSI), national institute (DIN).  Diverse HTML
    structures stress the acquire + filter stack.

Difficulty levers vs. Task 2
  - depth      : 1
  - questions  : 4  (HQ / founded / domains / member count)
  - q-types    : single x2 + long-list x1 + conditional-single x1
  - entities   : 6
  - null risk  : Q4 is "None (not disclosed)" for NIST and may be for BSI/DIN —
                 hallucination pressure on the extract layer.

Run:
    python tasks/task3_standards_bodies/build_input.py
"""
import os
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "input.xlsx")

# (entity, seed_url)
ROWS = [
    ("ISO",       "https://www.iso.org/home.html"),
    ("IEEE",      "https://www.ieee.org/about/"),
    ("IEC",       "https://www.iec.ch/about"),
    ("NIST",      "https://www.nist.gov/about-nist"),
    ("BSI Group", "https://www.bsigroup.com/"),
    ("DIN",       "https://www.din.de/en/about-din"),
]

entities_df = pd.DataFrame({"entity": [e for e, _ in ROWS]})

urls_df = pd.DataFrame({
    "url":      [u for _, u in ROWS],
    "depth":    [1] * len(ROWS),
    "entities": [e for e, _ in ROWS],
})

questions_df = pd.DataFrame({
    "question": [
        "Headquarters location",
        "Year established",
        "Technical domains covered",
        "Member or participant count",
    ],
    "instructions": [
        "In which city and country is this organization's headquarters located? "
        "Check about or contact pages.",

        "In what year was this organization founded, established, or created? "
        "Return only the 4-digit year.",

        "What are the main technical sectors, disciplines, or subject areas "
        "this organization develops standards for? "
        "List each distinct area separately; do not combine multiple areas.",

        "How many member countries, national bodies, national committees, or "
        "member organizations does this body have? "
        "Only extract a number if it is explicitly stated on the website. "
        "If no member count is mentioned, do not guess or invent a number.",
    ],
})

config_df = pd.DataFrame({
    "setting": ["EXTRACT_TOOL", "CRAWL_MAX_PAGES"],
    "value":   ["azure",        "15"],
})

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    entities_df.to_excel(w, sheet_name="entities",  index=False)
    urls_df.to_excel(w,      sheet_name="urls",      index=False)
    questions_df.to_excel(w, sheet_name="questions", index=False)
    config_df.to_excel(w,    sheet_name="config",    index=False)

print(f"Wrote {OUT}  ({len(ROWS)} entities, depth=1, 4 questions)")
