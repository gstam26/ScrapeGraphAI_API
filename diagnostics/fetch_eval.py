"""
Fetch quality diagnostic.

Compares static HTTP fetching and Playwright-rendered fetching, with and
without Trafilatura extraction. This script is diagnostic-only: it does not
import or modify the production acquire fetcher, crawler, or link scorer.

Usage:
    python diagnostics/fetch_eval.py
    python diagnostics/fetch_eval.py https://example.com https://example.org
    python diagnostics/fetch_eval.py --urls-file targets.txt

Artifacts are written to:
    diagnostics/fetch_eval/<run-id>/<target-slug>/

Each target folder contains at least:
    static.html
    static.txt
    rendered.html
    rendered.txt
    report.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "diagnostics" / "fetch_eval"
DEFAULT_URLS = [
    "https://www.oatly.com/oatly-who/sustainability-plan/sustainability-report",
    "https://ripplefoods.com/pages/our-story",
    "https://www.califiafarms.com/sustainability",
    "https://silk.com/about-us/sustainability/",
    "https://elmhurst1925.com/blogs/news/elmhurst-earth-month-sustainability",
    "https://www.mintel.com/food-and-drink/plant-based-milk",
]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 fetch-quality-diagnostic"
}

LOW_TEXT_CHARS = 1000
LOW_TEXT_WORDS = 120
RENDER_GAIN_RATIO = 1.5
RENDER_GAIN_CHARS = 500


@dataclass
class FetchData:
    ok: bool
    html: str = ""
    status_code: int | None = None
    final_url: str = ""
    redirects: list[dict[str, Any]] | None = None
    content_type: str = ""
    elapsed_ms: int = 0
    error: str = ""


@dataclass
class MethodResult:
    method: str
    status_code: int | None
    final_url: str
    redirects: list[dict[str, Any]]
    content_type: str
    page_title: str
    html_size: int
    extracted_text_size: int
    extracted_word_count: int
    elapsed_ms: int
    extraction_success: bool
    error: str


def _load_optional(name: str):
    try:
        return __import__(name)
    except Exception:
        return None


def _slug_for_url(url: str, index: int) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "") or "url"
    path = parsed.path.strip("/") or "home"
    raw = f"{index:02d}-{host}-{path}"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-").lower()
    return slug[:120] or f"{index:02d}-target"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _title(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return " ".join(soup.title.string.split())
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


def _size(text: str) -> int:
    return len((text or "").encode("utf-8"))


def _word_count(text: str) -> int:
    return len((text or "").split())


def _redirects_from_httpx(response: httpx.Response) -> list[dict[str, Any]]:
    redirects = []
    for item in response.history:
        redirects.append({
            "status_code": item.status_code,
            "url": str(item.url),
            "location": item.headers.get("location", ""),
        })
    return redirects


def _fetch_static(url: str, timeout: int) -> FetchData:
    started = time.perf_counter()
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers=REQUEST_HEADERS,
        ) as client:
            response = client.get(url)
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchData(
            ok=response.is_success,
            html=response.text or "",
            status_code=response.status_code,
            final_url=str(response.url),
            redirects=_redirects_from_httpx(response),
            content_type=response.headers.get("content-type", ""),
            elapsed_ms=elapsed,
            error="" if response.is_success else f"HTTP {response.status_code}",
        )
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchData(ok=False, elapsed_ms=elapsed, error=str(exc), redirects=[])


def _playwright_redirects(response: Any) -> list[dict[str, Any]]:
    redirects: list[dict[str, Any]] = []
    try:
        request = response.request if response else None
        current = request
        chain = []
        while current is not None and getattr(current, "redirected_from", None):
            previous = current.redirected_from
            chain.append(previous)
            current = previous
        for request_item in reversed(chain):
            redirects.append({
                "status_code": None,
                "url": request_item.url,
                "location": "",
            })
    except Exception:
        return []
    return redirects


def _fetch_rendered(url: str, timeout: int, wait_ms: int) -> FetchData:
    started = time.perf_counter()
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchData(
            ok=False,
            elapsed_ms=elapsed,
            error=f"Playwright unavailable: {exc}",
            redirects=[],
        )

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers=REQUEST_HEADERS)
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            html = page.content()
            final_url = page.url
            status_code = response.status if response else None
            headers = response.headers if response else {}
            elapsed = int((time.perf_counter() - started) * 1000)
            return FetchData(
                ok=bool(html) and (status_code is None or 200 <= status_code < 400),
                html=html or "",
                status_code=status_code,
                final_url=final_url,
                redirects=_playwright_redirects(response),
                content_type=headers.get("content-type", ""),
                elapsed_ms=elapsed,
                error=_rendered_fetch_error(status_code, html),
            )
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return FetchData(ok=False, elapsed_ms=elapsed, error=str(exc), redirects=[])
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass


def _extract_trafilatura(html: str, url: str) -> tuple[str, str]:
    trafilatura = _load_optional("trafilatura")
    if trafilatura is None:
        return "", "trafilatura is not installed"
    if not html:
        return "", "no HTML to extract"
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="txt",
            include_comments=False,
            include_tables=True,
        )
    except Exception as exc:
        return "", str(exc)
    if not extracted:
        return "", "trafilatura returned no text"
    return extracted.strip(), ""


def _rendered_fetch_error(status_code: int | None, html: str) -> str:
    if not html:
        return "no rendered HTML returned"
    if status_code is not None and not (200 <= status_code < 400):
        return f"HTTP {status_code}"
    return ""


def _method_result(
    method: str,
    fetch: FetchData,
    text: str,
    extraction_elapsed_ms: int,
    extraction_error: str = "",
) -> MethodResult:
    error = "; ".join(part for part in [fetch.error, extraction_error] if part)
    return MethodResult(
        method=method,
        status_code=fetch.status_code,
        final_url=fetch.final_url,
        redirects=fetch.redirects or [],
        content_type=fetch.content_type,
        page_title=_title(fetch.html),
        html_size=_size(fetch.html),
        extracted_text_size=_size(text),
        extracted_word_count=_word_count(text),
        elapsed_ms=fetch.elapsed_ms + extraction_elapsed_ms,
        extraction_success=bool(text.strip()) and not extraction_error and fetch.ok,
        error=error,
    )


def _evaluate_target(url: str, target_dir: Path, timeout: int, wait_ms: int) -> dict[str, Any]:
    static = _fetch_static(url, timeout)
    rendered = _fetch_rendered(url, timeout, wait_ms)

    _write_text(target_dir / "static.html", static.html)
    _write_text(target_dir / "rendered.html", rendered.html)

    started = time.perf_counter()
    static_text = _html_to_text(static.html)
    static_text_elapsed = int((time.perf_counter() - started) * 1000)
    _write_text(target_dir / "static.txt", static_text)

    started = time.perf_counter()
    static_traf_text, static_traf_error = _extract_trafilatura(static.html, static.final_url or url)
    static_traf_elapsed = int((time.perf_counter() - started) * 1000)
    _write_text(target_dir / "static.trafilatura.txt", static_traf_text)

    started = time.perf_counter()
    rendered_text = _html_to_text(rendered.html)
    rendered_text_elapsed = int((time.perf_counter() - started) * 1000)
    _write_text(target_dir / "rendered.txt", rendered_text)

    started = time.perf_counter()
    rendered_traf_text, rendered_traf_error = _extract_trafilatura(rendered.html, rendered.final_url or url)
    rendered_traf_elapsed = int((time.perf_counter() - started) * 1000)
    _write_text(target_dir / "rendered.trafilatura.txt", rendered_traf_text)

    methods = [
        _method_result("HTTP fetch (httpx)", static, static_text, static_text_elapsed),
        _method_result(
            "HTTP fetch + Trafilatura extraction",
            static,
            static_traf_text,
            static_traf_elapsed,
            static_traf_error,
        ),
        _method_result("Playwright-rendered fetch", rendered, rendered_text, rendered_text_elapsed),
        _method_result(
            "Playwright-rendered fetch + Trafilatura extraction",
            rendered,
            rendered_traf_text,
            rendered_traf_elapsed,
            rendered_traf_error,
        ),
    ]

    recommendation = _recommend(methods)
    target_report = {
        "url": url,
        "output_dir": str(target_dir),
        "recommendation": recommendation,
        "methods": [asdict(method) for method in methods],
        "artifacts": {
            "static_html": str(target_dir / "static.html"),
            "static_text": str(target_dir / "static.txt"),
            "static_trafilatura_text": str(target_dir / "static.trafilatura.txt"),
            "rendered_html": str(target_dir / "rendered.html"),
            "rendered_text": str(target_dir / "rendered.txt"),
            "rendered_trafilatura_text": str(target_dir / "rendered.trafilatura.txt"),
            "report_json": str(target_dir / "report.json"),
        },
    }
    _write_text(target_dir / "report.json", json.dumps(target_report, indent=2, sort_keys=True))
    return target_report


def _best_chars(methods: list[MethodResult], prefix: str) -> int:
    return max(
        (m.extracted_text_size for m in methods if m.method.startswith(prefix) and m.extraction_success),
        default=0,
    )


def _fetch_ok(methods: list[MethodResult], prefix: str) -> bool:
    return any(
        m.html_size > 0 and m.status_code is not None and 200 <= m.status_code < 400
        for m in methods
        if m.method.startswith(prefix)
    )


def _recommend(methods: list[MethodResult]) -> dict[str, str]:
    static_best = _best_chars(methods, "HTTP")
    rendered_best = _best_chars(methods, "Playwright")
    best = max(static_best, rendered_best)
    static_fetch_ok = _fetch_ok(methods, "HTTP")
    rendered_fetch_ok = _fetch_ok(methods, "Playwright")

    if not static_fetch_ok and not rendered_fetch_ok:
        return {
            "category": "FETCH_FAILED",
            "rationale": "Neither static nor rendered fetching produced usable HTML.",
        }

    if best < LOW_TEXT_CHARS:
        return {
            "category": "LOW_TEXT_YIELD",
            "rationale": (
                f"Best extracted text was {best} chars, below the {LOW_TEXT_CHARS} char "
                f"diagnostic threshold."
            ),
        }

    render_gain = rendered_best - static_best
    if rendered_best >= LOW_TEXT_CHARS and (
        static_best == 0
        or (rendered_best >= static_best * RENDER_GAIN_RATIO and render_gain >= RENDER_GAIN_CHARS)
    ):
        return {
            "category": "NEEDS_RENDER",
            "rationale": (
                f"Rendered extraction produced {rendered_best} chars versus "
                f"{static_best} static chars."
            ),
        }

    return {
        "category": "STATIC_OK",
        "rationale": (
            f"Static extraction produced {static_best} chars and rendering did not "
            f"clear the configured gain threshold."
        ),
    }


def _short(text: str, width: int) -> str:
    text = str(text or "")
    return text if len(text) <= width else text[: width - 1] + "~"


def _print_target_table(target: dict[str, Any]) -> None:
    print()
    print(f"URL: {target['url']}")
    print(f"Artifacts: {target['output_dir']}")
    print(
        "  "
        + f"{'METHOD':<48} {'HTTP':>5} {'HTML':>9} {'TEXT':>9} "
        + f"{'WORDS':>7} {'MS':>7} {'OK':>3}  TITLE / ERROR"
    )
    print(
        "  "
        + f"{'-' * 48} {'-' * 5:>5} {'-' * 9:>9} {'-' * 9:>9} "
        + f"{'-' * 7:>7} {'-' * 7:>7} {'-' * 3:>3}  {'-' * 28}"
    )
    for method in target["methods"]:
        note = method["error"] or method["page_title"]
        print(
            "  "
            + f"{_short(method['method'], 48):<48} "
            + f"{str(method['status_code'] or ''):>5} "
            + f"{method['html_size']:>9} "
            + f"{method['extracted_text_size']:>9} "
            + f"{method['extracted_word_count']:>7} "
            + f"{method['elapsed_ms']:>7} "
            + f"{'yes' if method['extraction_success'] else 'no':>3}  "
            + _short(note, 80)
        )
    rec = target["recommendation"]
    print(f"  Recommendation: {rec['category']} - {rec['rationale']}")


def _read_urls_file(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if clean and not clean.startswith("#"):
            urls.append(clean)
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare fetch quality across static and rendered strategies.")
    parser.add_argument("urls", nargs="*", help="URLs to evaluate. Defaults to the sample workbook targets.")
    parser.add_argument("--urls-file", type=Path, help="Text file with one URL per line.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=int, default=30, help="Fetch timeout in seconds.")
    parser.add_argument("--wait-ms", type=int, default=3000, help="Extra wait after Playwright DOM load.")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.urls_file:
        urls.extend(_read_urls_file(args.urls_file))
    if not urls:
        urls = DEFAULT_URLS

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(run_dir),
        "thresholds": {
            "low_text_chars": LOW_TEXT_CHARS,
            "low_text_words": LOW_TEXT_WORDS,
            "render_gain_ratio": RENDER_GAIN_RATIO,
            "render_gain_chars": RENDER_GAIN_CHARS,
        },
        "dependencies": {
            "httpx": True,
            "beautifulsoup4": True,
            "trafilatura": _load_optional("trafilatura") is not None,
            "playwright": _load_optional("playwright") is not None,
        },
        "targets": [],
    }

    print()
    print("FETCH EVALUATION")
    print(f"Output: {run_dir}")
    print(f"Targets: {len(urls)}")
    print(
        "Dependencies: "
        + ", ".join(f"{name}={'yes' if ok else 'no'}" for name, ok in report["dependencies"].items())
    )

    for index, url in enumerate(urls, start=1):
        target_dir = run_dir / _slug_for_url(url, index)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = _evaluate_target(url, target_dir, timeout=args.timeout, wait_ms=args.wait_ms)
        report["targets"].append(target)
        _print_target_table(target)

    _write_text(run_dir / "report.json", json.dumps(report, indent=2, sort_keys=True))

    print()
    print("SUMMARY")
    counts: dict[str, int] = {}
    for target in report["targets"]:
        category = target["recommendation"]["category"]
        counts[category] = counts.get(category, 0) + 1
    for category in ("STATIC_OK", "NEEDS_RENDER", "LOW_TEXT_YIELD", "FETCH_FAILED"):
        print(f"  {category:<15} {counts.get(category, 0)}")
    print(f"  JSON report     {run_dir / 'report.json'}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
