"""Build the full ADLM 182-company input workbook from matched_official_urls.csv.

Schema mirrors src/io_excel.read_input():
  entities: entity | urls: url, depth, entities | questions: question, instructions
  | config: setting, value

Entity naming: company names with commas break the reader (urls `entities` column is
comma-split, and the entities sheet requires exact subset matching). So commas are
stripped from names — e.g. "ALine, Inc." -> "ALine Inc.". Cosmetic only; used as the
Matrix row label and the extraction entity key.

Batching (credit budgeting — Firecrawl allowance may not cover all 182 in one go):
Run from repo root (reads matched_official_urls.csv relative to CWD):
    python scripts/build_182_workbook.py                 # full set
    python scripts/build_182_workbook.py --start 1 --end 70    # batch 1 -> adlm_182_input_1-70.xlsx
    python scripts/build_182_workbook.py --start 71            # batch 2 -> adlm_182_input_71-<end>.xlsx
Slicing happens AFTER dedup, so ranges are stable across invocations.
"""
import argparse

import pandas as pd

SRC = "matched_official_urls.csv"

parser = argparse.ArgumentParser(description="Build the ADLM 182 input workbook (optionally a batch slice)")
parser.add_argument("--start", type=int, default=1, help="1-based first company (after dedup), inclusive")
parser.add_argument("--end", type=int, default=None, help="1-based last company (after dedup), inclusive")
args = parser.parse_args()


def clean_entity(name: str) -> str:
    return " ".join(str(name).replace(",", " ").split())


df = pd.read_csv(SRC)
# Drop exact duplicate rows (bioMerieux, NEB, QuidelOrtho, Siemens appear twice).
df = df.drop_duplicates(subset=["company", "official_url"]).reset_index(drop=True)

total = len(df)
end = args.end if args.end is not None else total
if not (1 <= args.start <= end <= total):
    raise SystemExit(f"Bad slice: --start {args.start} --end {end} (have {total} companies after dedup)")
df = df.iloc[args.start - 1:end].reset_index(drop=True)

OUT = (
    "adlm-inputs/adlm_182_input.xlsx"
    if (args.start, end) == (1, total)
    else f"adlm-inputs/adlm_182_input_{args.start}-{end}.xlsx"
)

df["entity"] = df["company"].map(clean_entity)

# Guard: entity names must be unique (entities sheet keys on them).
dupes = df["entity"][df["entity"].duplicated()].tolist()
if dupes:
    raise SystemExit(f"Duplicate entity names after comma-strip: {dupes}")

entities_df = pd.DataFrame({"entity": df["entity"]})
urls_df = pd.DataFrame({
    "url": df["official_url"],
    "depth": [1] * len(df),
    "entities": df["entity"],
})

questions_df = pd.DataFrame({
    "question": ["R&D location", "Company type", "Diagnostics type", "Recent news"],
    "instructions": [
        "In which country or countries does the company conduct its R&D? List each "
        "location separately; include city or region if stated. Check headquarters, "
        "locations, laboratories, or about pages.",
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

# Azure-direct extraction (GPT-4.1-mini), George's decision 2026-07-13: the
# Power Automate llmapi proxy now serves the same model, so the extra flow
# dependency buys nothing. Explicit row (not just the config.py default) so
# the client-facing workbook states its own extractor.
config_df = pd.DataFrame({"setting": ["EXTRACT_TOOL"], "value": ["azure"]})

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    entities_df.to_excel(w, sheet_name="entities", index=False)
    urls_df.to_excel(w, sheet_name="urls", index=False)
    questions_df.to_excel(w, sheet_name="questions", index=False)
    config_df.to_excel(w, sheet_name="config", index=False)

print(f"Wrote {OUT}: {len(df)} companies (slice {args.start}-{end} of {total} after dedup), depth=1, 4 questions.")
