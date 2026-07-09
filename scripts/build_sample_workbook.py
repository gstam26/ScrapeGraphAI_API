"""One-off builder for the clinical-diagnostics sample input workbook.

Schema mirrors exactly what src/io_excel.read_input() expects:
  - sheet "entities":  column "entity"
  - sheet "urls":      columns "url", "depth", "entities"
  - sheet "questions": columns "question", "instructions"
  - sheet "config":    columns "setting", "value"  (only _SUPPORTED_CONFIG_KEYS)
"""
import pandas as pd

OUT = "sample_diagnostics_input.xlsx"

# (company, url, why-it's-in-the-spread)
ROWS = [
    # --- messy / stress URLs ---
    ("Hamilton Company",            "https://www.hamiltoncompany.com/adlm2024"),                                   # event/campaign deep-link
    ("Hettich Instruments",         "https://www.hettweb.com/adlm-clinical-lab-expo-2025/"),                       # event page + ALIAS domain (hettweb != hettich)
    ("Tosoh Bioscience",            "https://lab.tosoh.com/adlm-2026"),                                            # event subdomain deep-link
    ("Siemens Healthineers",        "https://events.siemens-healthineers.com/adlm"),                               # events. subdomain campaign
    ("Burkert Fluid Control Systems","https://www.burkert-usa.com/en/products/microfluidics-products-and-pumps?n=1"),# deep product path + query string
    ("BD",                          "https://bd.com/vacutainer"),                                                  # bare domain (no www) + deep-link
    ("Surmodics IVD",               "https://shop.surmodics.com/"),                                                # shop. subdomain (transactional)
    ("Ahlstrom Filtration LLC",     "https://www.ahlstrom.com/products/medical-life-sciences-and-laboratory/"),    # deep product path
    ("Sanzay Corp",                 "http://idgone.com"),                                                          # alias/unrelated-looking domain, http
    ("Bio-Techne Diagnostics",      "https://www.bio-techne.com/diagnostics"),                                     # deep path
    ("Colorcon",                    "https://www.colorcon.com/industries/diagnostics"),                            # deep path
    # --- clean homepage controls ---
    ("bioMerieux",                  "http://www.biomerieux.com"),                                                  # clean homepage, http
    ("Randox Laboratories",         "https://www.randox.com"),                                                     # clean control
    ("Abbott",                      "https://www.corelaboratory.abbott/"),                                         # subdomain but clean homepage
]

entities_df = pd.DataFrame({"entity": [c for c, _ in ROWS]})

urls_df = pd.DataFrame({
    "url":      [u for _, u in ROWS],
    "depth":    [1] * len(ROWS),
    "entities": [c for c, _ in ROWS],   # comma-free names -> no split hazard in _parse_entity_list
})

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

# EXTRACT_TOOL is in _SUPPORTED_CONFIG_KEYS, so it CAN be set here. This makes the
# workbook self-documenting: the proxy/GPT-5.5 path (llmapi) is selected explicitly.
# NOTE: FILTER_MODE is NOT a supported config key and cannot be set from Excel.
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
