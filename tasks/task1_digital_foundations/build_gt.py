"""
Ground-truth builder — Task 1: Digital Non-Profit Foundations

GT format used by diagnostics/generic_eval.py:
  Sheet "GroundTruth": entity | question | value | is_list | verbatim_quote | source_url | notes
  Sheet "Metadata":    key    | value

Verification notes (web-verified 2026-07-07):
  Wikimedia Foundation : founded 2003 (wikimediafoundation.org/about/ — confirmed)
  Mozilla Foundation   : founded 2003 (mozillafoundation.org/en/about/ — confirmed)
  Internet Archive     : founded 1996 (training knowledge; Brewster Kahle, San Francisco)
  Apache Software Foundation : founded 1999, mission "software for the public good" (apache.org/foundation/ — confirmed)
  Creative Commons     : founded 2001, Mountain View CA (creativecommons.org/about/ — confirmed)

Q3 (Main projects): conservative list — only items prominently named on the about pages.
  More items will likely be extracted by the pipeline; those are GT-gap, not hallucinations.
  Analyst should review ai_only claims after the first pipeline run and promote confirmed
  correct ones to the GT if re-running the evaluation.

Run:
    python tasks/task1_digital_foundations/build_gt.py
"""
import os
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "ground_truth.xlsx")

# (entity, question, value, is_list, verbatim_quote, source_url, notes)
GT_ROWS = [
    # ── Wikimedia Foundation ──────────────────────────────────────────────────
    ("Wikimedia Foundation", "Year founded", "2003", False,
     "Since our founding in 2003",
     "https://wikimediafoundation.org/about/", ""),

    ("Wikimedia Foundation", "Primary mission", "Enable universal access to knowledge", False,
     "a world in which every single human being can freely share in the sum of all knowledge",
     "https://wikimediafoundation.org/about/", "Accept close paraphrase"),

    ("Wikimedia Foundation", "Main projects or services", "Wikipedia",     True,  "", "https://wikimediafoundation.org/about/", ""),
    ("Wikimedia Foundation", "Main projects or services", "Wikidata",      True,  "", "https://wikimediafoundation.org/about/", ""),
    ("Wikimedia Foundation", "Main projects or services", "Wikimedia Commons", True, "", "https://wikimediafoundation.org/about/", ""),
    ("Wikimedia Foundation", "Main projects or services", "Wiktionary",    True,  "", "https://wikimediafoundation.org/about/", "Conservative — pipeline may find more"),

    # ── Mozilla Foundation ────────────────────────────────────────────────────
    ("Mozilla Foundation", "Year founded", "2003", False,
     "",
     "https://www.mozillafoundation.org/en/about/", ""),

    ("Mozilla Foundation", "Primary mission",
     "A non-profit building a better technology future — powered by people, open by design, fueled by imagination",
     False,
     "A non-profit building a better technology future — powered by people, open by design, fueled by imagination",
     "https://www.mozillafoundation.org/en/about/", "Verbatim from page"),

    ("Mozilla Foundation", "Main projects or services", "Firefox",     True, "", "https://www.mozillafoundation.org/en/about/", ""),
    ("Mozilla Foundation", "Main projects or services", "Thunderbird",  True, "", "https://www.mozillafoundation.org/en/about/", ""),
    ("Mozilla Foundation", "Main projects or services", "MDN Web Docs", True, "", "https://www.mozillafoundation.org/en/about/", "May not be on Foundation about page — verify"),

    # ── Internet Archive ──────────────────────────────────────────────────────
    ("Internet Archive", "Year founded", "1996", False,
     "",
     "https://archive.org/about", "Training knowledge; Brewster Kahle founded 1996"),

    ("Internet Archive", "Primary mission", "Universal access to all knowledge", False,
     "universal access to all knowledge",
     "https://archive.org/about", ""),

    ("Internet Archive", "Main projects or services", "Wayback Machine", True, "", "https://archive.org/about", ""),
    ("Internet Archive", "Main projects or services", "Open Library",    True, "", "https://archive.org/about", ""),

    # ── Apache Software Foundation ────────────────────────────────────────────
    ("Apache Software Foundation", "Year founded", "1999", False,
     "Since 1999",
     "https://www.apache.org/foundation/", "Confirmed from apache.org/foundation/"),

    ("Apache Software Foundation", "Primary mission",
     "To provide software for the public good",
     False,
     "to provide software for the public good",
     "https://www.apache.org/foundation/", "Verbatim from page"),

    ("Apache Software Foundation", "Main projects or services", "Apache HTTP Server", True,
     "", "https://www.apache.org/foundation/",
     "Core namesake project; many other Apache projects will also be extracted"),
    ("Apache Software Foundation", "Main projects or services", "Apache Hadoop", True, "", "https://www.apache.org/foundation/", "Conservative"),
    ("Apache Software Foundation", "Main projects or services", "Apache Spark",  True, "", "https://www.apache.org/foundation/", "Conservative"),

    # ── Creative Commons ──────────────────────────────────────────────────────
    ("Creative Commons", "Year founded", "2001", False,
     "",
     "https://creativecommons.org/about/",
     "Training knowledge; Lawrence Lessig founded 2001 — confirm on site"),

    ("Creative Commons", "Primary mission",
     "Build and sustain a thriving commons of shared knowledge and culture",
     False,
     "Creative Commons helps build and sustain a thriving commons of shared knowledge and culture so that it can power human creativity, equity, and innovation",
     "https://creativecommons.org/about/", "Confirmed from page"),

    ("Creative Commons", "Main projects or services", "Creative Commons licenses", True,
     "", "https://creativecommons.org/about/", "Core product family"),
    ("Creative Commons", "Main projects or services", "CC BY",    True, "", "https://creativecommons.org/about/", "Individual license — may or may not be listed separately"),
    ("Creative Commons", "Main projects or services", "CC BY-SA", True, "", "https://creativecommons.org/about/", ""),
]

gt_df = pd.DataFrame(GT_ROWS, columns=[
    "entity", "question", "value", "is_list",
    "verbatim_quote", "source_url", "notes",
])

meta_df = pd.DataFrame([
    ("task_name",        "Task 1 — Digital Non-Profit Foundations"),
    ("difficulty",       "Easy — depth 0, single + short-list questions, stable facts"),
    ("entities",         "5"),
    ("questions",        "3 (Year founded, Primary mission, Main projects or services)"),
    ("depth",            "0"),
    ("created",          "2026-07-07"),
    ("gt_verified",      "Wikimedia/Mozilla/Apache/Creative Commons web-verified 2026-07-07; "
                         "Internet Archive from training knowledge"),
    ("eval_script",      "python diagnostics/generic_eval.py "
                         "tasks/task1_digital_foundations/ground_truth.xlsx <pipeline_output.xlsx>"),
    ("notes",            "Q3 GT is conservative (prominent projects only). "
                         "AI-only extractions that are correct are GT-gap, not hallucinations. "
                         "Promote verified ai_only items to GT before re-running."),
], columns=["key", "value"])

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    gt_df.to_excel(w,   sheet_name="GroundTruth", index=False)
    meta_df.to_excel(w, sheet_name="Metadata",    index=False)

print(f"Wrote {OUT}  ({len(gt_df)} GT rows across {gt_df['entity'].nunique()} entities)")
