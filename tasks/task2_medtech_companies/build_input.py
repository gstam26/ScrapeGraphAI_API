"""
Task 2 — Medical Device Companies  (MEDIUM / Depth-1)

6 large medtech companies; pipeline must crawl one level beyond the seed URL
(about/leadership/products pages) to fill all 4 questions.

Difficulty levers vs. Task 1
  - depth     : 1  (BFS to linked pages, max 10 pages per entity)
  - questions : 4  (HQ / founded / product areas / CEO)
  - q-types   : single-answer x3 + list x1
  - entities  : 6

Q3 (product areas) is the list question — tests filter routing to products pages
and multi-item extraction with dedup.

Q4 (CEO) is single-answer and time-sensitive.  GT is stamped 2026-07-07; verify
before reusing GT for runs more than ~6 months later.

Run:
    python tasks/task2_medtech_companies/build_input.py
"""
import os
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "input.xlsx")

# (entity, seed_url)  — company homepages; depth=1 lets the crawler follow
# about / leadership / products sub-pages automatically
ROWS = [
    ("Medtronic",          "https://www.medtronic.com/"),
    ("Stryker",            "https://www.stryker.com/"),
    ("Boston Scientific",  "https://www.bostonscientific.com/en-US/about-us.html"),
    ("Edwards Lifesciences", "https://www.edwards.com/"),
    ("Abbott",             "https://www.abbott.com/"),
    ("Zimmer Biomet",      "https://www.zimmerbiomet.com/"),
]

entities_df = pd.DataFrame({"entity": [e for e, _ in ROWS]})

urls_df = pd.DataFrame({
    "url":      [u for _, u in ROWS],
    "depth":    [1] * len(ROWS),
    "entities": [e for e, _ in ROWS],
})

questions_df = pd.DataFrame({
    "question": [
        "Global headquarters",
        "Year founded",
        "Main product areas or therapy areas",
        "Current CEO",
    ],
    "instructions": [
        "In which city and country is the company's global or corporate headquarters "
        "located? Check about, company, or contact pages.",

        "In what year was this company founded or established? "
        "Return only the 4-digit year.",

        "What are the main product categories, business divisions, or medical specialty "
        "areas this company focuses on? List each area separately — do not merge "
        "multiple areas into one answer.",

        "What is the full name of the company's current Chief Executive Officer (CEO) "
        "or President? Check leadership or about pages.",
    ],
})

config_df = pd.DataFrame({
    "setting": ["EXTRACT_TOOL", "CRAWL_MAX_PAGES"],
    "value":   ["azure",        "10"],
})

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    entities_df.to_excel(w, sheet_name="entities",  index=False)
    urls_df.to_excel(w,      sheet_name="urls",      index=False)
    questions_df.to_excel(w, sheet_name="questions", index=False)
    config_df.to_excel(w,    sheet_name="config",    index=False)

print(f"Wrote {OUT}  ({len(ROWS)} entities, depth=1, 4 questions)")
