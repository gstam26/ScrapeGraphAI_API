"""Convert the CMO case-study client sheet to a pipeline input workbook.

The client file is a single 'CMOs' sheet: header on row 4 (0-indexed 3),
entity column 'CMO', seed column 'Website', 15 question columns between them,
all answer cells blank. Questions carry NO separate instructions yet (per
plan: baseline first, instructions added later), so the questions sheet is
emitted with empty instructions — the extractor sees the column header
verbatim.

Real-input hygiene handled here (all observed in the actual file):
  - whitespace/newlines inside URLs ("www.biplas.com\\n")
  - scheme-less URLs (www.avenuemould.com)
  - deep division links are KEPT as given (carclo.co.uk/our-businesses/... points
    at the CMO division; root-normalising would crawl the parent plc instead)

--check probes every cleaned URL (GET, honest UA, 10 s timeout, one request
per domain so no politeness concern) and classifies each entity into a cohort:
  ok / redirected (records the final URL) / http_<status> / unreachable / missing
The full inventory is written as a CSV next to the workbook — that CSV is the
authoritative working/broken/missing count and the input for the resolve step
(scripts/resolve_urls.py) that finds URLs for the missing/broken cohorts.

Only cohort-ok/redirected entities go into the pipeline workbook (a crawl
needs a live seed). Client data note: cmo-inputs/ falls under the .gitignore
*.xlsx / *.csv blanket — nothing here is committed unless deliberately
un-ignored.

Usage (from repo root):
    python scripts/build_cmo_workbook.py path/to/client.xlsx --check
    python scripts/build_cmo_workbook.py client.xlsx --depth 1 --start 1 --end 5
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENTITY_COL = "CMO"
URL_COL = "Website"
HEADER_ROW = 3
OUT_DIR = "cmo-inputs"


def clean_entity(name: str) -> str:
    # Commas break the reader (urls `entities` column is comma-split).
    return " ".join(str(name).replace(",", " ").split())


def clean_url(raw) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    url = str(raw).strip()
    if not url or url.lower() == "nan":
        return ""
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def probe(url: str) -> tuple[str, str]:
    """Return (cohort, final_url). One polite GET per URL (each is its own domain)."""
    import httpx
    try:
        r = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 entity-extraction-pipeline"},
            timeout=10,
            follow_redirects=True,
        )
        final = str(r.url)
        if r.status_code < 400:
            same = final.rstrip("/") == url.rstrip("/")
            return ("ok" if same else "redirected"), final
        return f"http_{r.status_code}", final
    except Exception as e:
        return f"unreachable ({type(e).__name__})", ""


def main() -> int:
    ap = argparse.ArgumentParser(description="CMO client sheet -> pipeline input workbook")
    ap.add_argument("source", help="path to the client xlsx")
    ap.add_argument("--check", action="store_true",
                    help="probe every URL and classify cohorts (recommended first run)")
    ap.add_argument("--depth", type=int, default=1, help="crawl depth per seed (default 1)")
    ap.add_argument("--start", type=int, default=1,
                    help="1-based first usable entity, inclusive (after cohort filter)")
    ap.add_argument("--end", type=int, default=None, help="1-based last usable entity, inclusive")
    ap.add_argument("--entities", default=None,
                    help="comma-separated exact entity names — for a fixed sample "
                         "reused across depth-sweep runs (overrides --start/--end)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="CRAWL_MAX_PAGES override written to the workbook config sheet. "
                         "The 2026-07-13 sweep showed the default budget (15/entity) fills "
                         "entirely with depth-1 pages (BFS), so depth 2 never runs — raise "
                         "this to make depth>=2 measurable at all.")
    ap.add_argument("--out-name", default=None,
                    help="output filename override (default derived from the slice)")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    df = pd.read_excel(args.source, header=HEADER_ROW).dropna(how="all").reset_index(drop=True)
    for col in (ENTITY_COL, URL_COL):
        if col not in df.columns:
            sys.exit(f"expected column {col!r} not found — got {list(df.columns)}")
    df = df[df[ENTITY_COL].notna()].reset_index(drop=True)

    questions = [c for c in df.columns if c not in (ENTITY_COL, URL_COL)]
    df["entity"] = df[ENTITY_COL].map(clean_entity)
    df["url"] = df[URL_COL].map(clean_url)

    # The client list repeats some companies (observed: Flextronics x3,
    # Partnertech x3, ...), with at most one URL among the copies. Consolidate
    # to one row per entity name, preferring a row that has a URL. Name-level
    # only — near-duplicates with different names ("Flextronics" vs
    # "Flextronics International Ltd.") are NOT merged; that's entity
    # resolution guesswork this converter must not do silently.
    before = len(df)
    df = (
        df.sort_values("url", ascending=False)  # non-empty URLs first
          .drop_duplicates(subset=["entity"], keep="first")
          .sort_index()
          .reset_index(drop=True)
    )
    if len(df) < before:
        print(f"Consolidated {before - len(df)} duplicate rows "
              f"({before} -> {len(df)} unique entities)")

    # ── Inventory (with optional live probe) ────────────────────────────────
    if args.check:
        with_url = df[df["url"] != ""]
        print(f"Probing {len(with_url)} URLs (parallel, one request per domain)...")
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(probe, with_url["url"]))
        cohorts = dict(zip(with_url.index, results))
        df["cohort"] = [
            "missing" if u == "" else cohorts[i][0] for i, u in df["url"].items()
        ]
        df["final_url"] = [
            "" if u == "" else cohorts[i][1] for i, u in df["url"].items()
        ]
    else:
        df["cohort"] = ["missing" if u == "" else "unchecked" for u in df["url"]]
        df["final_url"] = ""

    os.makedirs(args.out_dir, exist_ok=True)
    inv_path = os.path.join(args.out_dir, "cmo_url_inventory.csv")
    df[["entity", "url", "cohort", "final_url"]].to_csv(inv_path, index=False)

    print(f"\n{len(df)} entities, {len(questions)} questions")
    print(df["cohort"].value_counts().to_string())
    print(f"\nInventory written: {inv_path}")

    # ── Pipeline workbook for the usable cohort ─────────────────────────────
    usable_cohorts = {"ok", "redirected", "unchecked"}
    usable = df[df["cohort"].isin(usable_cohorts)].reset_index(drop=True)
    if usable.empty:
        print("No usable entities — resolve URLs first (scripts/resolve_urls.py).")
        return 1
    # Redirected sites: seed the final URL (the site moved; crawl where it lives now).
    usable["seed"] = [
        (f if c == "redirected" and f else u)
        for u, c, f in zip(usable["url"], usable["cohort"], usable["final_url"])
    ]

    if args.entities:
        wanted = [e.strip() for e in args.entities.split(",") if e.strip()]
        usable_by_name = usable.set_index("entity")
        missing = [e for e in wanted if e not in usable_by_name.index]
        if missing:
            sys.exit(
                f"--entities not in the usable cohort (not ok/redirected, or misspelled): "
                f"{missing}\nUsable: {sorted(usable['entity'])}"
            )
        # Preserve the order given, not file order — same list, same order,
        # every depth-sweep run, so runs are directly comparable row-for-row.
        usable = usable_by_name.loc[wanted].reset_index()
        out_name = args.out_name or f"cmo_input_named_depth{args.depth}.xlsx"
    else:
        total = len(usable)
        end = args.end if args.end is not None else total
        if not (1 <= args.start <= end <= total):
            sys.exit(f"bad slice --start {args.start} --end {end} (have {total} usable)")
        usable = usable.iloc[args.start - 1:end].reset_index(drop=True)
        out_name = args.out_name or (
            "cmo_input.xlsx" if (args.start, end) == (1, total)
            else f"cmo_input_{args.start}-{end}.xlsx"
        )
    out_path = os.path.join(args.out_dir, out_name)

    entities_df = pd.DataFrame({"entity": usable["entity"]})
    urls_df = pd.DataFrame({
        "url": usable["seed"], "depth": [args.depth] * len(usable),
        "entities": usable["entity"],
    })
    questions_df = pd.DataFrame({
        "question": questions,
        "instructions": [""] * len(questions),  # baseline: no instructions yet
    })
    config_rows = [("EXTRACT_TOOL", "azure")]
    if args.max_pages is not None:
        config_rows.append(("CRAWL_MAX_PAGES", args.max_pages))
    config_df = pd.DataFrame(config_rows, columns=["setting", "value"])

    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        entities_df.to_excel(w, sheet_name="entities", index=False)
        urls_df.to_excel(w, sheet_name="urls", index=False)
        questions_df.to_excel(w, sheet_name="questions", index=False)
        config_df.to_excel(w, sheet_name="config", index=False)

    slice_desc = (
        f"named: {', '.join(usable['entity'])}" if args.entities
        else f"slice {args.start}-{end} of {total} usable"
    )
    print(f"Workbook written: {out_path} — {len(usable)} entities ({slice_desc}), "
          f"depth={args.depth}, {len(questions)} questions, extraction=azure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
