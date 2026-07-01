"""Controlled comparison workbook: clean www homepages for the SAME companies
that starved/stressed Q1 in the messy sample.

The ONLY variable that moves vs. sample_diagnostics_input.xlsx is the seed URL
(messy event/shop/alias seed -> clean homepage). Entity names, the four questions,
depth=1, and the EXTRACT_TOOL=llmapi config are identical, so any change in Q1
(R&D location) recall is attributable to the URL swap, not to anything else.
"""
import pandas as pd

OUT = "adlm-inputs/sample_clean_homepages_input.xlsx"

# (entity name — IDENTICAL to the messy workbook,  clean homepage,  messy seed it replaces)
ROWS = [
    # --- subdomain / alias seeds that trapped the crawl (Q1 starved) ---
    ("Siemens Healthineers",   "https://www.siemens-healthineers.com/",  "was events.siemens-healthineers.com/adlm"),
    ("Tosoh Bioscience",       "https://www.tosohbioscience.com/",       "was lab.tosoh.com/adlm-2026"),
    ("Hettich Instruments",    "https://www.hettweb.com/",               "was hettweb.com/adlm-clinical-lab-expo-2025/ (event path)"),
    ("Surmodics IVD",          "https://www.surmodics.com/",             "was shop.surmodics.com"),
    # --- same-domain controls (never subdomain-trapped; deep-link -> homepage) ---
    ("Bio-Techne Diagnostics", "https://www.bio-techne.com/",            "was bio-techne.com/diagnostics (found Q1 already)"),
    ("Colorcon",               "https://www.colorcon.com/",              "was colorcon.com/industries/diagnostics (Q1 was blank)"),
]

entities_df = pd.DataFrame({"entity": [e for e, _, _ in ROWS]})

urls_df = pd.DataFrame({
    "url":      [u for _, u, _ in ROWS],
    "depth":    [1] * len(ROWS),
    "entities": [e for e, _, _ in ROWS],
})

# Questions — copied VERBATIM from build_sample_workbook.py, unchanged.
questions_df = pd.DataFrame({
    "question": [
        "R&D location",
        "Company type",
        "Diagnostics type",
        "Recent news",
    ],
    "instructions": [
        "In which country or countries does the company conduct its R&D? Include city or "
        "region if stated. Check headquarters, locations, laboratories, or about pages.",
        "Does the company develop and market its own branded diagnostic products, or does "
        "it make products for other companies (OEM / contract manufacturing / white-label)? "
        "Answer own-product, OEM/contract, or both, based on how the company describes itself.",
        "Which types of clinical diagnostics does the company provide? List each distinct "
        "diagnostic area, technology, or assay type separately.",
        "What recent news or announcements has the company published — product launches, "
        "regulatory clearances, funding, partnerships, and similar? List each item "
        "separately, with its date if given.",
    ],
})

# Identical config to the messy sample (controlled comparison).
config_df = pd.DataFrame({
    "setting": ["EXTRACT_TOOL"],
    "value":   ["llmapi"],
})

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    entities_df.to_excel(w, sheet_name="entities", index=False)
    urls_df.to_excel(w, sheet_name="urls", index=False)
    questions_df.to_excel(w, sheet_name="questions", index=False)
    config_df.to_excel(w, sheet_name="config", index=False)

print(f"Wrote {OUT} with {len(ROWS)} companies, depth=1, 4 questions.")
