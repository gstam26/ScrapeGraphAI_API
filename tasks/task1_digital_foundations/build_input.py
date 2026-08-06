"""
Task 1 — Digital Non-Profit Foundations  (EASY / Depth-0 baseline)

5 well-known non-profit foundations; all answers live on a single hand-picked
about page (depth=0).  Designed to measure pure extraction quality with zero
crawl noise — a clean baseline before adding crawl complexity.

Difficulty levers
  - depth     : 0  (no BFS; only the seed URL is fetched)
  - questions : 3  (founded year / mission / main projects)
  - q-types   : single-answer x2 + list x1
  - entities  : 5

Run:
    python tasks/task1_digital_foundations/build_input.py
"""
import os
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "input.xlsx")

# (entity, seed_url)
ROWS = [
    ("Wikimedia Foundation", "https://wikimediafoundation.org/about/"),
    ("Mozilla Foundation",   "https://www.mozillafoundation.org/en/about/"),
    ("Internet Archive",     "https://archive.org/about"),
    ("Apache Software Foundation", "https://www.apache.org/foundation/"),
    ("Creative Commons",     "https://creativecommons.org/about/"),
]

entities_df = pd.DataFrame({"entity": [e for e, _ in ROWS]})

urls_df = pd.DataFrame({
    "url":      [u for _, u in ROWS],
    "depth":    [0] * len(ROWS),
    "entities": [e for e, _ in ROWS],
})

questions_df = pd.DataFrame({
    "question": [
        "Year founded",
        "Primary mission",
        "Main projects or services",
    ],
    "instructions": [
        "Extract the year the organization was founded or established. "
        "Return only the 4-digit year.",

        "Extract the organization's stated primary mission or purpose. "
        "Use the exact wording from the page where possible.",

        "List the main projects, products, tools, or services this organization "
        "offers or is responsible for. Name each item separately; do not combine "
        "multiple projects into a single answer.",
    ],
})

config_df = pd.DataFrame({
    "setting": ["EXTRACT_TOOL", "ACQUIRE_TOOL"],
    "value":   ["azure",        "local"],
})

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    entities_df.to_excel(w, sheet_name="entities",  index=False)
    urls_df.to_excel(w,      sheet_name="urls",      index=False)
    questions_df.to_excel(w, sheet_name="questions", index=False)
    config_df.to_excel(w,    sheet_name="config",    index=False)

print(f"Wrote {OUT}  ({len(ROWS)} entities, depth=0, 3 questions)")
