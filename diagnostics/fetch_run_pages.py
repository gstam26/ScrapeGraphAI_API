"""Fetch every page of a pipeline run's Acquire Log into a local text cache.

Built for the 2026-07-23 CE answerability A/B; generally useful whenever an
analysis needs the page TEXTS of a run executed on another machine (the
output workbook records URLs but not texts). Resumable: pages already in the
target dir are skipped (fetch errors are cached as empty files so reruns
don't retry them). Politeness (robots.txt, per-domain delay) enforced by the
pooled hybrid fetcher.

Usage:
    python diagnostics/fetch_run_pages.py <run_workbook.xlsx> <out_dir>
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from dotenv import load_dotenv
load_dotenv()

import pandas as pd

from models import Config
from src.acquire.cache import read_cache, write_cache
from src.acquire.fetcher import fetch_page_with_provenance

if len(sys.argv) != 3:
    sys.exit("usage: fetch_run_pages.py <run_workbook.xlsx> <out_dir>")
WB, OUT_DIR = sys.argv[1], sys.argv[2]

a = pd.read_excel(WB, "Acquire Log")
urls = list(dict.fromkeys(a["Page URL"].dropna()))
cfg = Config(acquire_tool="playwright_pooled_hybrid")
print(f"{len(urls)} pages -> {OUT_DIR}")

ok = err = skipped = 0
for i, url in enumerate(urls, 1):
    if read_cache(url, OUT_DIR) is not None:
        skipped += 1
        continue
    try:
        text, _, prov = fetch_page_with_provenance(url, cfg)
        write_cache(url, text, OUT_DIR)
        ok += 1
        print(f"[{i}/{len(urls)}] ok {len(text)} chars "
              f"{'(rescued)' if 'rescue' in (prov['gate_reason'] or '') else ''} {url}")
    except Exception as e:
        write_cache(url, "", OUT_DIR)  # record the miss so reruns skip it
        err += 1
        print(f"[{i}/{len(urls)}] ERROR {url}: {e}")

print(f"\ndone: {ok} fetched, {skipped} already present, {err} errors")
