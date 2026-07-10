"""Fetch-backend bake-off: playwright_pooled vs a Firecrawl baseline run.

Approved by Nick 2026-07-02 for TESTING (priority: quality). Method: re-fetch
the EXACT page URLs a completed Firecrawl run selected (from its Acquire Log),
so the comparison measures pure fetch quality — no crawler/scorer variance,
no LLM needed, no API keys. Politeness gate (per-domain delay, robots.txt,
honest UA) is enforced by the playwright_pooled backend itself.

Per page: fetch success, extracted chars vs Firecrawl chars, quality-gate
result, same-domain links discovered from the rendered DOM vs Firecrawl's
candidate count, fetch time. Per entity + overall: summary and verdict inputs.

Interpretation notes (written into the output):
- chars are NOT like-for-like: Firecrawl markdown keeps nav/link text that
  Trafilatura strips as boilerplate, so pw/fc char ratios well below 1.0 can
  still mean full content. Very low ratios (<0.3) flag pages to inspect.
- link counts favour whoever sees the fuller DOM; pw counts come from the
  same _discover_links_from_html used in production.

Usage:
    python diagnostics/backend_compare.py                       # default 8 entities
    python diagnostics/backend_compare.py --entities "Catachem,Bruker"
    python diagnostics/backend_compare.py --baseline path.xlsx --out out.xlsx
    python diagnostics/backend_compare.py --backend playwright_pooled_hybrid

With --backend playwright_pooled_hybrid the per-page "PW Backend" column
records which path served each page (pooled_hybrid_static vs
pooled_hybrid_render) and the summary reports the static-hit rate — the
hybrid's efficiency claim, measured. Politeness is identical on both paths.
"""
import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Config
from src.acquire.crawler import _discover_links_from_html
from src.acquire.fetcher import _fetch_playwright_pooled, _fetch_playwright_pooled_hybrid

_BACKENDS = {
    "playwright_pooled": _fetch_playwright_pooled,
    "playwright_pooled_hybrid": _fetch_playwright_pooled_hybrid,
}

DEFAULT_BASELINE = "adlm-outputs/validation_sample_run_2026-07-02_v2.xlsx"

# Quality-focused selection: the two zero-crawl failures (biggest potential
# upside for a real browser), the giant-page case, and a corporate/light mix.
DEFAULT_ENTITIES = [
    "FUJIFILM Healthcare Americas Corporation",
    "Catachem",
    "Bruker",
    "HORIBA",
    "Sebia",
    "Hologic",
    "Metrohm USA",
    "Acro Biotech Inc.",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="self-hosted backend vs Firecrawl baseline")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--out", default=None, help="output xlsx (default: adlm-outputs/backend_compare_<backend>_vs_firecrawl.xlsx)")
    ap.add_argument("--entities", default=None, help="comma-separated entity names (default: 8-entity quality mix)")
    ap.add_argument("--backend", default="playwright_pooled", choices=sorted(_BACKENDS),
                    help="self-hosted backend to compare against the Firecrawl baseline")
    args = ap.parse_args()
    fetch = _BACKENDS[args.backend]
    out_path = args.out or f"adlm-outputs/backend_compare_{args.backend}_vs_firecrawl.xlsx"

    entities = (
        [e.strip() for e in args.entities.split(",") if e.strip()]
        if args.entities else DEFAULT_ENTITIES
    )

    xl = pd.ExcelFile(args.baseline)
    aq = pd.read_excel(xl, "Acquire Log")
    cc = pd.read_excel(xl, "Crawl Candidates")

    cfg = Config(acquire_tool=args.backend)
    rows: list[dict] = []

    for ent in entities:
        sub = aq[aq["Entities"] == ent]
        if sub.empty:
            print(f"!! entity not in baseline Acquire Log: {ent!r}")
            continue
        seed = str(sub.iloc[0]["Seed URL"])
        print(f"\n=== {ent} ({len(sub)} pages, seed {seed})")

        for _, r in sub.iterrows():
            url = str(r["Page URL"])
            fc_chars = int(r["Page Length (chars)"])
            fc_cands = int((cc[(cc["Entities"] == ent) & (cc["Parent URL"] == url)]).shape[0])

            t0 = time.time()
            err = ""
            try:
                text, html, prov = fetch(url, cfg)
            except Exception as e:
                text, html, err = "", None, f"{type(e).__name__}: {str(e)[:160]}"
                prov = {"backend": args.backend, "gate_passed": False, "gate_reason": "fetch_error"}
            ms = int((time.time() - t0) * 1000)

            pw_links = len(_discover_links_from_html(url, seed, 1, html)) if html else 0
            pw_chars = len(text)
            ratio = round(pw_chars / fc_chars, 2) if fc_chars else None

            status = "error" if err else ("gate_failed" if prov["gate_passed"] is False else "ok")
            print(f"  [{status:11}] {ms:6} ms  {pw_chars:7,} ch (fc {fc_chars:7,}, x{ratio})"
                  f"  links pw {pw_links:3} / fc {fc_cands:3}  {url[:70]}")

            rows.append({
                "Entity": ent, "Page URL": url, "PW Status": status, "PW Error": err,
                "PW Backend": prov.get("backend", args.backend),
                "PW Gate Reason": prov.get("gate_reason", ""),
                "PW Chars": pw_chars, "FC Chars": fc_chars, "Chars Ratio": ratio,
                "PW Links Discovered": pw_links, "FC Candidates": fc_cands,
                "PW Fetch (ms)": ms, "FC Fetch (ms)": int(r["Fetch Time (ms)"]),
                "Depth": int(r["Depth"]),
            })

    df = pd.DataFrame(rows)
    if df.empty:
        print("No pages compared."); return 1

    ok = df["PW Status"] == "ok"
    per_ent = df.groupby("Entity").agg(
        pages=("Page URL", "count"),
        pw_ok=("PW Status", lambda s: int((s == "ok").sum())),
        med_ratio=("Chars Ratio", "median"),
        pw_links=("PW Links Discovered", "sum"),
        fc_cands=("FC Candidates", "sum"),
        pw_med_ms=("PW Fetch (ms)", "median"),
        fc_med_ms=("FC Fetch (ms)", "median"),
    )
    print("\n================ SUMMARY ================")
    print(per_ent.to_string())
    print(f"\nOverall: {int(ok.sum())}/{len(df)} pages ok "
          f"({ok.mean()*100:.1f}%) | median chars ratio {df['Chars Ratio'].median()} "
          f"| links pw {int(df['PW Links Discovered'].sum())} vs fc {int(df['FC Candidates'].sum())} "
          f"| median fetch pw {int(df['PW Fetch (ms)'].median())} ms vs fc {int(df['FC Fetch (ms)'].median())} ms")
    low = df[ok & (df["Chars Ratio"] < 0.3)]
    if len(low):
        print(f"\nPages to inspect (ratio < 0.3): {len(low)}")
        for _, r in low.iterrows():
            print(f"  x{r['Chars Ratio']} {r['Page URL'][:80]}")

    if args.backend == "playwright_pooled_hybrid":
        static_n = int((df["PW Backend"] == "pooled_hybrid_static").sum())
        print(f"\nHybrid static-hit rate: {static_n}/{len(df)} pages "
              f"({static_n / len(df) * 100:.1f}%) served without launching the browser")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Pages", index=False)
        per_ent.reset_index().to_excel(w, sheet_name="Per Entity", index=False)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
