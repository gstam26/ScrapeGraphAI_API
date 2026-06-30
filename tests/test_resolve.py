"""Offline tests for the company-URL resolver.

These exercise the scoring core (rapidfuzz + keyword corroboration + blocklist)
and the resolver orchestration with the search layer monkeypatched, so no
network, Firecrawl, or Ollama access is required.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.resolve import confidence as conf
from src.resolve.confidence import (
    registrable_domain,
    is_blocked,
    score_name,
    score_content,
    score_candidate,
    decide_review,
)
from src.resolve.io_csv import read_input_csv, write_output_csv, OUTPUT_COLUMNS
from src.resolve.models import Candidate, CompanyInput, ResolutionResult
from src.resolve import resolver, search as search_mod


def _check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}: {name}")
    assert cond, name


def test_registrable_domain():
    cases = {
        "https://www.acme.com/about": "acme.com",
        "http://shop.acme.co.uk/x": "acme.co.uk",
        "acme.com": "acme.com",
        "https://sub.deep.example.org": "example.org",
        "https://news.bbc.co.uk": "bbc.co.uk",
        "": "",
    }
    for url, expected in cases.items():
        got = registrable_domain(url)
        _check(f"registrable_domain({url!r}) == {expected!r} (got {got!r})", got == expected)


def test_blocklist():
    _check("linkedin.com blocked", is_blocked("linkedin.com"))
    _check("uk.linkedin.com-derived label blocked", is_blocked("linkedin.co.uk"))
    _check("x.com blocked", is_blocked("x.com"))
    _check("crunchbase blocked", is_blocked("crunchbase.com"))
    _check("10times blocked", is_blocked("10times.com"))
    _check("real company not blocked", not is_blocked("acmerobotics.com"))
    # required-by-spec members all present
    for d in ["linkedin", "facebook", "twitter", "instagram", "crunchbase",
              "wikipedia", "amazon", "pubmed", "researchgate", "bloomberg"]:
        _check(f"blocklist label contains {d}", d in conf._BLOCKLIST_LABELS or any(d in b for b in conf.BLOCKLIST_DOMAINS))


def test_name_score():
    strong, _ = score_name("Acme Robotics Inc", "acmerobotics.com")
    weak, _ = score_name("Acme Robotics Inc", "totally-unrelated-domain.com")
    _check(f"matching domain scores high ({strong:.2f})", strong >= 0.8)
    _check(f"unrelated domain scores low ({weak:.2f})", weak < 0.5)
    _check("name>unrelated", strong > weak)


def test_content_score():
    inp = CompanyInput(
        company="Acme Robotics",
        description="industrial robot arms and automation",
        categories="Robotics, Automation",
    )
    good, _ = score_content(inp, "Acme Robotics builds industrial robot arms for automation")
    poor, _ = score_content(inp, "We sell artisanal cheese and dairy products")
    _check(f"on-topic text scores higher ({good:.2f} vs {poor:.2f})", good > poor)


def test_score_candidate_blocked_zeroed():
    inp = CompanyInput(company="Acme Robotics")
    c = Candidate(url="https://www.linkedin.com/company/acme-robotics", rank=0)
    score_candidate(c, inp)
    _check("blocked candidate flagged", c.blocked)
    _check(f"blocked candidate confidence 0 ({c.confidence})", c.confidence == 0.0)


def test_decide_review_confident_vs_ambiguous():
    a = Candidate(url="https://acme.com", domain="acme.com", confidence=0.9)
    b = Candidate(url="https://other.com", domain="other.com", confidence=0.3)
    needs, _ = decide_review([a, b])
    _check("clear winner not flagged", not needs)

    a2 = Candidate(url="https://acme.com", domain="acme.com", confidence=0.7)
    b2 = Candidate(url="https://acme.io", domain="acme.io", confidence=0.66)
    needs2, note2 = decide_review([a2, b2])
    _check(f"close pair flagged ambiguous ({note2})", needs2)

    low = Candidate(url="https://x.com", domain="x.com", confidence=0.4)
    needs3, _ = decide_review([low])
    _check("low-confidence flagged", needs3)


def test_csv_roundtrip(tmp_dir):
    in_path = os.path.join(tmp_dir, "in.csv")
    with open(in_path, "w", encoding="utf-8") as f:
        f.write("Company,Booth,Description,Categories\n")
        f.write("Acme Robotics,A12,Robot arms,Robotics\n")
        f.write(" ,,, \n")  # blank company should be skipped
    rows = read_input_csv(in_path)
    _check(f"reads 1 row, skips blank (got {len(rows)})", len(rows) == 1)
    _check("case-insensitive headers", rows[0].company == "Acme Robotics" and rows[0].booth == "A12")

    out_path = os.path.join(tmp_dir, "out.csv")
    write_output_csv(out_path, [ResolutionResult(
        company="Acme Robotics", resolved_url="https://acme.com",
        confidence=0.91, candidate_alternatives=["https://acme.io", "https://acme.ai"],
        needs_review=False, notes="confident",
    )])
    with open(out_path, encoding="utf-8") as f:
        head = f.readline().strip()
    _check("output header matches contract", head == ",".join(OUTPUT_COLUMNS))


def test_resolver_end_to_end_offline(monkeypatch_search):
    """Resolver picks the on-name domain over an aggregator, offline."""
    fake = [
        Candidate(url="https://www.linkedin.com/company/acme", rank=0, title="Acme | LinkedIn"),
        Candidate(url="https://www.acmerobotics.com", rank=1, title="Acme Robotics - Official",
                  snippet="Industrial robot arms and automation by Acme Robotics"),
        Candidate(url="https://acme-on-crunchbase.crunchbase.com", rank=2, title="Acme - Crunchbase"),
    ]
    monkeypatch_search(fake)

    inp = CompanyInput(company="Acme Robotics", description="robot arms automation",
                       categories="Robotics")
    res = resolver.resolve_company(inp, cfg=None, fetch_homepages=False)
    _check(f"resolved to official domain (got {res.resolved_url})",
           res.resolved_url == "https://www.acmerobotics.com")
    _check("aggregators excluded from alternatives",
           all("linkedin" not in u and "crunchbase" not in u for u in res.candidate_alternatives))


def test_resolver_no_results_reports_unresolved(monkeypatch_search):
    """If Firecrawl search returns nothing, the company is reported unresolved
    — never silently guessed via a fallback."""
    monkeypatch_search([])
    inp = CompanyInput(company="Totally Obscure Company With No Web Presence")
    res = resolver.resolve_company(inp, cfg=None, fetch_homepages=False)
    _check(f"resolved_url is empty when no candidates (got {res.resolved_url!r})",
           res.resolved_url == "")
    _check("needs_review True when no candidates", res.needs_review is True)


# --- tiny test harness (no pytest dependency required) ---------------------

def _run():
    failures = 0
    tmp = tempfile.mkdtemp(prefix="resolve_test_")

    def monkeypatch_search(candidates):
        search_mod.last_search_backend = "test"
        resolver.search_mod.search_company = lambda *a, **k: [c.model_copy() for c in candidates]

    simple = [
        test_registrable_domain, test_blocklist, test_name_score, test_content_score,
        test_score_candidate_blocked_zeroed, test_decide_review_confident_vs_ambiguous,
    ]
    for t in simple:
        print(f"\n{t.__name__}:")
        try:
            t()
        except AssertionError:
            failures += 1

    print("\ntest_csv_roundtrip:")
    try:
        test_csv_roundtrip(tmp)
    except AssertionError:
        failures += 1

    print("\ntest_resolver_end_to_end_offline:")
    orig = resolver.search_mod.search_company
    try:
        test_resolver_end_to_end_offline(monkeypatch_search)
    except AssertionError:
        failures += 1
    finally:
        resolver.search_mod.search_company = orig

    print("\ntest_resolver_no_results_reports_unresolved:")
    orig2 = resolver.search_mod.search_company
    try:
        test_resolver_no_results_reports_unresolved(monkeypatch_search)
    except AssertionError:
        failures += 1
    finally:
        resolver.search_mod.search_company = orig2

    print(f"\n{'ALL TESTS PASSED' if failures == 0 else f'{failures} TEST GROUP(S) FAILED'}")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if _run() else 0)
