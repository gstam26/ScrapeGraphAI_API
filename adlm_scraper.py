"""Free, standalone scraper for the ADLM 2026 exhibitor directory.

No API calls, no Firecrawl — plain requests + BeautifulSoup. Runs entirely
offline-of-paid-services on a work laptop.

Three phases, run independently via the CLI so the operator can inspect
Phase-2 coverage before any detail pages are fetched:

  phase1  Paginate the exhibitor list (AJAX, 40/page, ~18 pages, 716 rows) and
          write adlm_exhibitors_full.csv with company, booth, detail_page_slug
          (official_url / linkedin_url columns present but empty until Phase 3).

  phase2  Fuzzy-match the filtered input CSV (182 companies) against the
          scraped table on company name (rapidfuzz, style mirrored from
          src/resolve/confidence.py). Prints a coverage breakdown:
            >=95  confident   | 85-95  review   | <85  no match
          and writes a staging file phase2_match_staging.csv. STOPS here.

  phase3  For matched rows only, fetch each /co/{slug} detail page, extract the
          official URL + LinkedIn, and write matched_official_urls.csv. Also
          backfills those URLs into adlm_exhibitors_full.csv.

Pagination mechanism (investigated, see report):
  POST https://adlm26.myexpoonline.com/index.php
  module=organizations_organization_list, site_page_id=3000,
  method=paginationHandler, template=generic_items, mCell=0, mId=2,
  limit=40, offset=N, page_id=openAjax, tk=<csrf>, tm=<csrf>
  Response JSON: {total, formToken, formTime, data: <url-encoded HTML>}.
  tk/tm rotate every response (formToken/formTime) and must be chained.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

# Reuse the project's social/aggregator blocklist so a company's footer Twitter
# or a directory link is never mistaken for its official site.
from src.resolve.confidence import is_blocked, registrable_domain

# ============================================================
# Constants
# ============================================================

BASE = "https://adlm26.myexpoonline.com"
LIST_URL = f"{BASE}/exhibitors"
AJAX_URL = f"{BASE}/index.php"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PAGE_LIMIT = 40                       # rows per AJAX page (mId=2 table view)
REQUEST_PAUSE = 0.4                   # polite delay between requests (seconds)

HERE = Path(__file__).resolve().parent
FULL_CSV = HERE / "adlm_exhibitors_full.csv"
STAGING_CSV = HERE / "phase2_match_staging.csv"
MATCHED_CSV = HERE / "matched_official_urls.csv"

# Match thresholds (same-source match — expected near-perfect).
CONFIDENT_THRESHOLD = 95.0            # >= this: trust it
REVIEW_FLOOR = 85.0                   # [REVIEW_FLOOR, CONFIDENT): manual review
                                      # < REVIEW_FLOOR: treat as no match

# Event-platform host fragments — links to these are never an exhibitor's own
# site (the ADLM footer brand-bar and the A2Z platform).
_PLATFORM_HOST_TOKENS = (
    "myexpoonline", "pcomm.net", "jspargo", "mya2zevents", "a2zevents",
)

# ============================================================
# Session / token helpers
# ============================================================

def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def _extract_tokens(html: str) -> tuple[str, str]:
    """Pull the rotating CSRF tokens tk/tm from inline page JS."""
    tk = re.findall(r"(?<!\w)tk\s*=\s*['\"]([\w/+=,_-]+)['\"]\s*;", html)
    tm = re.findall(r"(?<!\w)tm\s*=\s*['\"]([\w/+=,_-]+)['\"]\s*;", html)
    if not tk or not tm:
        raise RuntimeError("Could not find tk/tm tokens on the exhibitor page "
                           "— the site markup may have changed.")
    return tk[0], tm[0]


# ============================================================
# Phase 1 — paginate the exhibitor table
# ============================================================

def _parse_booth(td) -> str:
    """Return booth as '#NNNN' from the map-marker anchor, or '' if absent."""
    a = td.find("a")
    if not a:
        return ""
    m = re.search(r"#\s*([\w-]+)", a.get_text(" ", strip=True))
    return f"#{m.group(1)}" if m else ""


# Two detail-page URL shapes appear in the table: the usual company microsite
# (/co/{slug}) and an exhibitor-record form (/exhibitors/exhibitor/{id}) used
# e.g. for companies with meeting-room-only booths (Abbott). Both pages carry
# the official URL, so we accept either as the detail_page_slug.
_DETAIL_HREF = re.compile(r"^/(co/|exhibitors/exhibitor/)")


def _parse_table_rows(html: str) -> list[dict]:
    """Extract one record per <tr> in a table-view HTML fragment."""
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        # The company cell holds the detail-page anchor; the booth cell holds
        # the map-marker (jspargo EventMap) link.
        co_anchor = tr.find("a", href=_DETAIL_HREF)
        if not co_anchor:
            continue
        company = co_anchor.get_text(" ", strip=True)
        slug = (co_anchor.get("data-option-url")
                or co_anchor.get("href") or "").strip()

        booth = ""
        for td in tds:
            if td.find("span", class_=re.compile(r"fa-map-marker")):
                booth = _parse_booth(td)
                break

        records.append({
            "company": company,
            "booth": booth,
            "official_url": "",      # filled in Phase 3 for matched rows
            "linkedin_url": "",      # filled in Phase 3 for matched rows
            "detail_page_slug": slug,
        })
    return records


def phase1(verbose: bool = True) -> list[dict]:
    """Paginate the full exhibitor list and write adlm_exhibitors_full.csv."""
    s = new_session()
    r0 = s.get(LIST_URL, timeout=30)
    r0.raise_for_status()
    tk, tm = _extract_tokens(r0.text)

    ajax_headers = {
        "Referer": LIST_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    all_rows: list[dict] = []
    seen_slugs: set[str] = set()
    offset = 0
    total = None
    page_no = 0

    while total is None or offset < total:
        page_no += 1
        params = {
            "module": "organizations_organization_list",
            "site_page_id": "3000",
            "method": "paginationHandler",
            "template": "generic_items",
            "mCell": "0",
            "mId": "2",
            "limit": str(PAGE_LIMIT),
            "offset": str(offset),
            "page_id": "openAjax",
            "tk": tk,
            "tm": tm,
        }
        resp = s.post(AJAX_URL, data=params, headers=ajax_headers, timeout=30)
        resp.raise_for_status()
        j = resp.json()

        if total is None:
            total = int(j.get("total", 0))
            if verbose:
                print(f"  total exhibitors reported: {total}")

        # Rotate tokens for the next request.
        tk = j.get("formToken", tk)
        tm = j.get("formTime", tm)

        rows = _parse_table_rows(unquote(j.get("data", "")))
        new = 0
        for rec in rows:
            key = rec["detail_page_slug"] or rec["company"]
            if key in seen_slugs:
                continue
            seen_slugs.add(key)
            all_rows.append(rec)
            new += 1

        if verbose:
            print(f"  page {page_no:>2}  offset {offset:>4}  "
                  f"+{new:>2} rows  (cumulative {len(all_rows)})")

        if not rows:
            print("  empty page returned — stopping early.")
            break

        offset += PAGE_LIMIT
        time.sleep(REQUEST_PAUSE)

    all_rows.sort(key=lambda d: d["company"].lower())
    _write_full_csv(all_rows)
    if verbose:
        print(f"\nPhase 1 complete: {len(all_rows)} exhibitors -> {FULL_CSV.name}")
    return all_rows


def _write_full_csv(rows: list[dict]) -> None:
    cols = ["company", "booth", "official_url", "linkedin_url",
            "detail_page_slug"]
    with FULL_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


# Verified manual overrides: input company name -> directory slug, for cases
# where the same company is listed under a different/abbreviated name and so
# falls below the fuzzy threshold. Each entry has been confirmed by hand
# (matching the detail-page official URL). Keyed on the raw input string.
MATCH_OVERRIDES: dict[str, str] = {
    # Directory lists it as just "Currier"; detail page is CurrierPlastics.com.
    "Currier Plastics, Inc.": "/co/currier",
}


# ============================================================
# Name matching (style mirrored from src/resolve/confidence.py)
# ============================================================

def _normalise_name(name: str) -> str:
    """Lowercase, collapse punctuation to spaces; keep ALL tokens.

    Deliberately *unlike* confidence._normalise_name, which strips legal
    suffixes for a name<->domain comparison. This is a same-source name<->name
    match, so the suffixes are identical on both sides and carry signal.
    Stripping them is actively harmful here: tokens like 'ab' (Aktiebolag) and
    'sa' (Societe Anonyme) are also real brand words, so 'AB Medical' would
    reduce to 'medical' and spuriously subset-match many longer names.
    """
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


def _name_similarity(a: str, b: str) -> float:
    """Similarity of two company names on the full normalised string (0..100).

    Uses full-string `ratio` plus an order-robust `token_sort_ratio`, taking
    the max. Both compare the WHOLE name, so a short directory name that is
    merely a token-subset of a longer input (the failure mode of
    token_set_ratio / partial_ratio / WRatio) does NOT score 100 — exactly what
    we want for a same-source match that should be near-exact or not a match.
    """
    na, nb = _normalise_name(a), _normalise_name(b)
    if not na or not nb:
        return 0.0
    return float(max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(na, nb)))


def best_match(company: str, table: list[dict]) -> tuple[dict | None, float]:
    """Return (best table record, score 0..100) for an input company name."""
    best_rec, best_score = None, -1.0
    for rec in table:
        sc = _name_similarity(company, rec["company"])
        if sc > best_score:
            best_rec, best_score = rec, sc
    return best_rec, max(best_score, 0.0)


# ============================================================
# Phase 2 — match input CSV against the scraped table, report coverage
# ============================================================

def _detect_company_column(header: list[str]) -> str:
    """Pick the column holding company names from an input CSV header."""
    lowered = {h.lower().strip(): h for h in header}
    for cand in ("company", "company_name", "name", "exhibitor",
                 "organization", "organisation"):
        if cand in lowered:
            return lowered[cand]
    # Fall back to the first column.
    return header[0]


def _load_input_companies(input_csv: Path) -> tuple[list[str], str]:
    with input_csv.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError(f"{input_csv} has no header row.")
        col = _detect_company_column(list(reader.fieldnames))
        companies = [(row.get(col) or "").strip() for row in reader]
    companies = [c for c in companies if c]
    return companies, col


def _load_table() -> list[dict]:
    if not FULL_CSV.exists():
        raise RuntimeError(f"{FULL_CSV.name} not found — run phase1 first.")
    with FULL_CSV.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def phase2(input_csv: Path) -> dict:
    """Match input companies to table rows; print coverage; write staging CSV."""
    companies, col = _load_input_companies(input_csv)
    table = _load_table()

    print(f"Input CSV:  {input_csv}")
    print(f"  company column detected: '{col}'")
    print(f"  input companies: {len(companies)}")
    print(f"  scraped table rows: {len(table)}\n")

    by_slug = {r["detail_page_slug"]: r for r in table}
    staged: list[dict] = []
    confident = review = no_match = overridden = 0
    for comp in companies:
        if comp in MATCH_OVERRIDES:
            # Verified by hand — bypass the fuzzy score entirely.
            slug = MATCH_OVERRIDES[comp]
            rec = by_slug.get(slug)
            staged.append({
                "input_company": comp,
                "matched_company": rec["company"] if rec else "",
                "match_score": 100.0,
                "detail_page_slug": slug,
                "bucket": "confident",
            })
            confident += 1
            overridden += 1
            continue

        rec, score = best_match(comp, table)
        score = round(score, 2)
        if score >= CONFIDENT_THRESHOLD:
            bucket = "confident"
            confident += 1
        elif score >= REVIEW_FLOOR:
            bucket = "review"
            review += 1
        else:
            bucket = "no_match"
            no_match += 1
        staged.append({
            "input_company": comp,
            "matched_company": rec["company"] if rec else "",
            "match_score": score,
            "detail_page_slug": rec["detail_page_slug"] if (
                rec and bucket != "no_match") else "",
            "bucket": bucket,
        })

    # Write staging file for Phase 3 to consume.
    with STAGING_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "input_company", "matched_company", "match_score",
            "detail_page_slug", "bucket"])
        w.writeheader()
        w.writerows(staged)

    total = len(companies)
    print("=" * 56)
    print("PHASE 2 COVERAGE")
    print("=" * 56)
    print(f"  >= {CONFIDENT_THRESHOLD:.0f}  confident : "
          f"{confident:>3} / {total}")
    print(f"  {REVIEW_FLOOR:.0f}-{CONFIDENT_THRESHOLD:.0f}  review    : "
          f"{review:>3} / {total}")
    print(f"  <  {REVIEW_FLOOR:.0f}  no match  : {no_match:>3} / {total}")
    if overridden:
        print(f"  (incl. {overridden} verified manual override"
              f"{'s' if overridden != 1 else ''})")
    print("-" * 56)
    print(f"  staging written -> {STAGING_CSV.name}")
    if review or no_match:
        print("\n  rows needing attention (score < "
              f"{CONFIDENT_THRESHOLD:.0f}):")
        for s in sorted(staged, key=lambda d: d["match_score"]):
            if s["bucket"] != "confident":
                print(f"    [{s['bucket']:>8}] {s['match_score']:>6.2f}  "
                      f"{s['input_company']!r}"
                      + (f"  ~  {s['matched_company']!r}"
                         if s["matched_company"] else "  ~  (none)"))
    return {"confident": confident, "review": review, "no_match": no_match,
            "total": total}


# ============================================================
# Phase 3 — fetch detail pages for matched rows, extract URLs
# ============================================================

def _is_platform_url(href: str) -> bool:
    """True for the event platform's own links (footer, A2Z, ADLM socials)."""
    low = href.lower()
    if "myadlm" in low:                       # ADLM's own social handles
        return True
    return any(t in low for t in _PLATFORM_HOST_TOKENS)


def _extract_detail_urls(html: str) -> tuple[str, str]:
    """Return (official_url, linkedin_url) from a detail page.

    The platform renders a fixed footer brand-bar of anchors with class
    'social_link' (ADLM's own Facebook/X/LinkedIn/YouTube + A2Z). The
    exhibitor's OWN links sit above it and lack that class: a plain website
    anchor and, when present, a 'social-media-link' LinkedIn. We therefore:
      - skip any 'social_link'-class anchor and any platform host;
      - take the first linkedin.com link as linkedin_url;
      - take the first remaining external link whose registrable domain is not
        a social/aggregator (reusing confidence.is_blocked) as official_url.
    Both stay empty when the exhibitor declared nothing (e.g. BizLink Elocab).
    """
    soup = BeautifulSoup(html, "html.parser")
    official, linkedin = "", ""
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().startswith("http"):
            continue
        if "social_link" in (a.get("class") or []):  # ADLM footer brand-bar
            continue
        if _is_platform_url(href):
            continue
        if "linkedin.com" in href.lower():
            if not linkedin:
                linkedin = href
            continue
        if is_blocked(registrable_domain(href)):      # other social/aggregator
            continue
        if not official:
            official = href
    return official, linkedin


def phase3(input_csv: Path | None = None) -> None:
    """Fetch detail pages for matched (non-no_match) rows; write outputs."""
    if not STAGING_CSV.exists():
        raise RuntimeError(f"{STAGING_CSV.name} not found — run phase2 first.")
    with STAGING_CSV.open("r", newline="", encoding="utf-8") as f:
        staged = list(csv.DictReader(f))

    to_fetch = [s for s in staged if s["bucket"] != "no_match"
                and s["detail_page_slug"]]
    print(f"Phase 3: fetching {len(to_fetch)} detail pages "
          f"(skipping {len(staged) - len(to_fetch)} unmatched)\n")

    s = new_session()
    results: list[dict] = []
    slug_to_urls: dict[str, tuple[str, str]] = {}
    for i, row in enumerate(to_fetch, 1):
        slug = row["detail_page_slug"]
        try:
            r = s.get(BASE + slug, timeout=30)
            r.raise_for_status()
            official, linkedin = _extract_detail_urls(r.text)
        except Exception as e:        # noqa: BLE001 — log and continue
            official, linkedin = "", ""
            print(f"  [{i:>3}] ERROR {slug}: {e}")
        slug_to_urls[slug] = (official, linkedin)

        score = float(row["match_score"])
        no_url = official == ""
        needs_review = (score < CONFIDENT_THRESHOLD) or no_url
        results.append({
            "company": row["input_company"],
            "official_url": official,
            "linkedin_url": linkedin,
            "match_score": row["match_score"],
            "detail_page_slug": slug,
            "needs_review": "yes" if needs_review else "no",
            # Distinguish the two failure modes the operator asked to separate.
            "review_reason": _review_reason(score, no_url),
        })
        flag = "" if not needs_review else "  <-- review"
        print(f"  [{i:>3}] {row['input_company'][:38]:<38} "
              f"{'URL' if official else 'NO-URL':>6}{flag}")
        time.sleep(REQUEST_PAUSE)

    # Append the unmatched companies so the output is complete (182 rows).
    for row in staged:
        if row["bucket"] == "no_match":
            results.append({
                "company": row["input_company"],
                "official_url": "",
                "linkedin_url": "",
                "match_score": row["match_score"],
                "detail_page_slug": "",
                "needs_review": "yes",
                "review_reason": "not_found_in_directory",
            })

    with MATCHED_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "company", "official_url", "linkedin_url", "match_score",
            "detail_page_slug", "needs_review", "review_reason"])
        w.writeheader()
        w.writerows(results)

    _backfill_full_csv(slug_to_urls)

    n_no_url = sum(1 for r in results
                   if r["review_reason"] == "no_url_declared")
    n_not_found = sum(1 for r in results
                      if r["review_reason"] == "not_found_in_directory")
    print(f"\nPhase 3 complete -> {MATCHED_CSV.name}")
    print(f"  with official URL : "
          f"{sum(1 for r in results if r['official_url'])}")
    print(f"  present, NO URL declared : {n_no_url}")
    print(f"  not found in directory   : {n_not_found}")


def _review_reason(score: float, no_url: bool) -> str:
    if score < REVIEW_FLOOR:
        return "not_found_in_directory"
    if score < CONFIDENT_THRESHOLD:
        return "low_match_score"
    if no_url:
        return "no_url_declared"
    return ""


def _backfill_full_csv(slug_to_urls: dict[str, tuple[str, str]]) -> None:
    """Write fetched URLs back into adlm_exhibitors_full.csv for matched rows."""
    if not FULL_CSV.exists():
        return
    rows = _load_table()
    for rec in rows:
        urls = slug_to_urls.get(rec["detail_page_slug"])
        if urls:
            rec["official_url"], rec["linkedin_url"] = urls
    _write_full_csv(rows)


# ============================================================
# CLI
# ============================================================

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="phase", required=True)

    sub.add_parser("phase1", help="scrape the full exhibitor directory")

    p2 = sub.add_parser("phase2", help="match input CSV, report coverage")
    p2.add_argument("input_csv", type=Path,
                    help="filtered input CSV (e.g. exhibitors_*.csv)")

    p3 = sub.add_parser("phase3", help="fetch detail pages for matched rows")

    args = p.parse_args(argv)
    if args.phase == "phase1":
        phase1()
    elif args.phase == "phase2":
        phase2(args.input_csv)
    elif args.phase == "phase3":
        phase3()
    return 0


if __name__ == "__main__":
    sys.exit(main())
