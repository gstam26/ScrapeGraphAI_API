"""Mid-size validation sample: measure real pages/entity and credits/entity for the
Firecrawl raw-HTML discovery fix, before committing ~2,000-2,700 credits to the full 182.

25 companies from matched_official_urls.csv, all NOT used in the earlier 14/6-company
runs, so every URL fetches fresh (no acquire-cache hits) and the measurement is clean.
Two buckets: corporate/JS-heavy (large-DOM, Surmodics-class) and light/simple.
depth=1, same 4 questions, EXTRACT_TOOL=llmapi. Comma-stripped entity names.
"""
import pandas as pd

SRC = "matched_official_urls.csv"
OUT = "adlm-inputs/validation_sample_input.xlsx"

CORPORATE = [  # large multinationals / big-DOM sites — expected to approach the 15-page cap
    "Agilent Technologies", "Bruker", "Danaher", "EUROIMMUN",
    "FUJIFILM Healthcare Americas Corporation", "Greiner Bio-One North America, Inc.",
    "Hologic", "HORIBA", "Metrohm USA", "Neogen", "Nova Biomedical", "QuidelOrtho",
    "Sartorius", "Sebia", "Shimadzu Scientific Instruments, Inc.", "Sysmex America",
    "Thermo Fisher Scientific", "McKesson Medical-Surgical",
]
LIGHT = [  # small companies / thin sites — expected to stay well under the cap
    "Aalto Scientific, Ltd.- Audit MicroControls", "Acro Biotech Inc.",
    "Aladdin Scientific", "Aniara Diagnostica", "Calbiotech, Inc.", "Catachem",
    "Monobind Inc.",
]

def clean_entity(name: str) -> str:
    return " ".join(str(name).replace(",", " ").split())

df = pd.read_csv(SRC).drop_duplicates(subset=["company", "official_url"])
by_company = dict(zip(df["company"], df["official_url"]))

rows = []
missing = []
for bucket, names in [("corporate", CORPORATE), ("light", LIGHT)]:
    for name in names:
        if name not in by_company:
            missing.append(name)
            continue
        rows.append((clean_entity(name), by_company[name], bucket))
if missing:
    raise SystemExit(f"Companies not found in CSV (fix names): {missing}")

# entity uniqueness guard
ents = [e for e, _, _ in rows]
if len(ents) != len(set(ents)):
    raise SystemExit("Duplicate entity names after comma-strip")

entities_df = pd.DataFrame({"entity": ents})
urls_df = pd.DataFrame({
    "url": [u for _, u, _ in rows],
    "depth": [1] * len(rows),
    "entities": ents,
})
questions_df = pd.DataFrame({
    "question": ["R&D location", "Company type", "Diagnostics type", "Recent news"],
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
config_df = pd.DataFrame({"setting": ["EXTRACT_TOOL"], "value": ["llmapi"]})

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    entities_df.to_excel(w, sheet_name="entities", index=False)
    urls_df.to_excel(w, sheet_name="urls", index=False)
    questions_df.to_excel(w, sheet_name="questions", index=False)
    config_df.to_excel(w, sheet_name="config", index=False)

n_corp = sum(1 for _, _, b in rows if b == "corporate")
n_light = sum(1 for _, _, b in rows if b == "light")
print(f"Wrote {OUT}: {len(rows)} companies ({n_corp} corporate + {n_light} light), depth=1.")
