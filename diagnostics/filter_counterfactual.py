"""Counterfactual: what would FILTER_MODE=threshold have done on a
passthrough run? Replays the routing rule (emb >= t OR keyword-gate,
fallback-all) over the Filter Log's recorded scores and overlays Provenance
claims to count what active filtering would have saved vs lost.

Works on any DIAGNOSTICS output workbook (passthrough logs full scores).
First result (2026-07-23, CMO 4-entity rescue run): threshold routing is a
no-op on CMO — 39/2023 pairs excluded at 0.55, scores compressed into
[0.455, 0.809], keyword gate fired on 60% of pairs; meanwhile 45/119 extract
calls yielded zero items and page-level embedding AUC for predicting any-yield
was only 0.63-0.67 (the real, unexploited saving — a CE answerability screen
is the recorded candidate).

Usage:
    python diagnostics/filter_counterfactual.py outputs/<run>.xlsx
"""
import sys

import pandas as pd

PATH = sys.argv[1]
THRESHOLDS = [0.45, 0.50, 0.55, 0.60]

fl = pd.read_excel(PATH, "Filter Log")
prov = pd.read_excel(PATH, "Provenance")
fl.columns = [c.strip() for c in fl.columns]

# Claims per (url, question). Provenance question = pipeline question name.
claims = prov.groupby(["Source URL", "Question"]).size()
verified = prov[prov["Verified"] == True].groupby(["Source URL", "Question"]).size()  # noqa: E712
notdisc = prov[prov["Claim"].astype(str).str.strip().str.lower() == "not disclosed"] \
    .groupby(["Source URL", "Question"]).size()

pairs = len(fl)
pages = fl["URL"].nunique()
print(f"{pages} pages x {fl['Column'].nunique()} questions = {pairs} routing decisions")
print(f"claims total: {len(prov)}  (verified {int((prov['Verified']==True).sum())}, "
      f"'Not disclosed' claims {int((prov['Claim'].astype(str).str.strip().str.lower()=='not disclosed').sum())})")

for t in THRESHOLDS:
    fl["inc"] = (fl["Embedding Score"] >= t) | (fl["Keyword Gate"] == True)  # noqa: E712
    # fallback-all: pages where nothing clears either gate route everything
    fallback_pages = fl.groupby("URL")["inc"].transform("any") == False  # noqa: E712
    fl.loc[fallback_pages, "inc"] = True

    excluded = fl[~fl["inc"]]
    lost = lost_v = lost_nd = 0
    for _, r in excluded.iterrows():
        key = (r["URL"], r["Column"])
        lost += int(claims.get(key, 0))
        lost_v += int(verified.get(key, 0))
        lost_nd += int(notdisc.get(key, 0))
    n_fallback = int(fallback_pages.sum() / fl["Column"].nunique())
    print(f"\n-- threshold {t}: excluded {len(excluded)}/{pairs} pairs "
          f"({len(excluded)/pairs:.0%}), fallback-all pages: {n_fallback}")
    print(f"   claims lost: {lost} ({lost/max(len(prov),1):.1%} of all) | "
          f"verified lost: {lost_v} | of lost, 'Not disclosed': {lost_nd}")

# Score distribution sanity + keyword gate fire rate
print("\nembedding score dist:", fl["Embedding Score"].describe()[["min","25%","50%","75%","max"]].round(3).to_dict())
print("keyword gate fires:", int((fl['Keyword Gate']==True).sum()), "/", pairs)  # noqa: E712

# Per-question exclusion at 0.55 with losses
fl["inc"] = (fl["Embedding Score"] >= 0.55) | (fl["Keyword Gate"] == True)  # noqa: E712
fb = fl.groupby("URL")["inc"].transform("any") == False  # noqa: E712
fl.loc[fb, "inc"] = True
print("\nper-question @0.55: excluded pairs | claims lost")
for q, g in fl.groupby("Column"):
    exc = g[~g["inc"]]
    lostq = sum(int(claims.get((r["URL"], r["Column"]), 0)) for _, r in exc.iterrows())
    print(f"  {q[:64]:64s} {len(exc):3d}/{len(g)}  lost {lostq}")
