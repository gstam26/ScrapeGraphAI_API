"""
Acquire-layer diagnostic.

Runs the acquire layer on an Excel input file, then optionally runs
filter/extract/verify/aggregate over the fetched pages to measure whether
crawl score predicts downstream usefulness. Saves a report Excel so you can
compare fetch backends, crawl quality, and contribution yield without
changing production crawl scoring.

Usage:
    python diagnostics/acquire_report.py samples/input1.xlsx
    python diagnostics/acquire_report.py samples/input1.xlsx --output outputs/acquire_report.xlsx
    python diagnostics/acquire_report.py samples/input1.xlsx --backend firecrawl
    python diagnostics/acquire_report.py samples/input1.xlsx --no-crawl
    python diagnostics/acquire_report.py samples/input1.xlsx --no-usefulness
"""

import argparse
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from config import (
    ACQUIRE_TOOL,
    CACHE_DIR,
    CRAWL_MAX_PAGES,
    CRAWL_MIN_SCORE,
    CRAWL_MIN_SCORE_EMBED,
    CRAWL_SCORER,
    DEFAULT_DEPTH,
    EXTRACT_PAGE_WORKERS,
    EXTRACT_TOOL,
    FILTER_THRESHOLD,
    REQUEST_HEADERS,
)
from src.io_excel import read_input
from models import Config, ExtractedCell, PageDoc
from src.acquire import FetchedPage, acquire
from src.aggregate import aggregate_cells
from src.extract import extract_cells
from src.filter import filter_page, score_page_columns, _keywords
from src.verify import verify_cells


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status_icon(status: str) -> str:
    return {"ok": "✓", "cached": "~", "gate_failed": "!", "error": "✗"}.get(status, "?")


def _bar(n: int, total: int, width: int = 20) -> str:
    filled = int(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _preview(text: str, chars: int = 120) -> str:
    t = text.strip().replace("\n", " ")
    return t[:chars] + "…" if len(t) > chars else t


# ── Filter scoring (diagnostic-only, mirrors src/filter.py logic) ─────────────

def _page_doc(fp: FetchedPage) -> PageDoc:
    return PageDoc(
        url=fp.url,
        text=fp.markdown,
        html=None,
        from_cache=fp.status == "cached",
        depth=fp.depth,
        crawl_score=fp.crawl_score,
        fetch_time_ms=fp.fetch_time_ms,
        backend=fp.backend,
        render_fallback=fp.render_fallback,
        gate_passed=fp.gate_passed,
        gate_reason=fp.gate_reason,
    )


def _page_title(text: str) -> str:
    for line in (text or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        if clean.startswith("#"):
            return clean.lstrip("#").strip()[:180]
        if len(clean.split()) <= 14:
            return clean[:180]
    return ""


def _avg(values: list[float]) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float))]
    return sum(clean) / len(clean) if clean else None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _legacy_filter_with_scores(pages: list, columns: list, threshold: float) -> list[dict]:
    """
    Embed each page and each question, return per-page routing with per-column
    cosine scores. Questions are embedded once for all pages.
    Raises on Ollama failure — caller wraps in try/except.
    """
    from src.embed import embed_batch

    all_names = [col.name for col in columns]
    if not all_names:
        return []

    question_texts = [OLLAMA_QUERY_PREFIX + name for name in all_names]
    q_embs = embed_batch(question_texts)

    results = []
    for p in pages:
        text = (p.markdown or "")[:2000]
        if not text:
            results.append({
                "url": p.url,
                "scores": {name: None for name in all_names},
                "relevant": set(all_names),
                "fallback": True,
            })
            continue

        page_emb = embed_batch([OLLAMA_DOC_PREFIX + text])[0]
        scores = {
            name: round(_cosine(page_emb, qv), 3)
            for name, qv in zip(all_names, q_embs)
        }
        relevant = {name for name, s in scores.items() if s >= threshold}
        fallback = not relevant
        if fallback:
            relevant = set(all_names)

        results.append({
            "url": p.url,
            "scores": scores,
            "relevant": relevant,
            "fallback": fallback,
        })

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def _filter_with_scores(pages: list, columns: list, threshold: float) -> list[dict]:
    """Production-aligned filter scoring for the acquire diagnostic."""
    all_names = [col.name for col in columns]
    if not all_names:
        return []

    results = []
    for p in pages:
        text = p.markdown or ""
        if not text:
            results.append({
                "url": p.url,
                "depth": p.depth,
                "scores": {name: None for name in all_names},
                "keyword_gate": {name: False for name in all_names},
                "relevant": set(all_names),
                "fallback": True,
            })
            continue

        raw_scores = score_page_columns(text, columns)
        scores = {name: round(raw_scores.get(name, 0.0), 3) for name in all_names}
        text_lower = text.lower()
        keyword_gate = {
            name: any(kw in text_lower for kw in _keywords(name))
            for name in all_names
        }
        relevant = {
            name for name in all_names
            if scores[name] >= threshold or keyword_gate[name]
        }
        fallback = not relevant
        if fallback:
            relevant = set(all_names)

        results.append({
            "url": p.url,
            "depth": p.depth,
            "scores": scores,
            "keyword_gate": keyword_gate,
            "relevant": relevant,
            "fallback": fallback,
        })

    return results


def _aggregate_final_evidence(cells: list[ExtractedCell], entities: list[str]) -> list:
    final_evidence = []
    grouped: dict[str, list[ExtractedCell]] = defaultdict(list)
    for cell in cells:
        grouped[cell.entity].append(cell)
    for entity in entities:
        for cell in aggregate_cells(grouped.get(entity, [])):
            for ev in cell.evidence:
                final_evidence.append((cell, ev))
    return final_evidence


def _build_usefulness_rows(
    pages: list[FetchedPage],
    columns: list,
    entities: list[str],
    cfg: Config,
    use_cache: bool,
) -> tuple[list[dict], list[dict]]:
    if not pages or not columns or not entities:
        return [], []

    page_docs = [_page_doc(fp) for fp in pages]
    all_cells: list[ExtractedCell] = []
    per_url_cells: dict[str, list[ExtractedCell]] = defaultdict(list)
    per_url_scores: dict[str, dict[str, float]] = {}
    per_url_errors: dict[str, str] = {}

    def process_page(index: int, page: PageDoc) -> dict:
        local_diag: dict = {"extract_log": [], "verify_log": []}
        try:
            raw_scores = score_page_columns(page.text or "", columns)
        except Exception as exc:
            raw_scores = {}
            local_diag["score_error"] = str(exc)

        try:
            routed = filter_page(page, columns)
            relevant_cols = [c for c in columns if c.name in routed.relevant_columns]
            cells = extract_cells(
                routed.page,
                relevant_cols,
                entities,
                cfg=cfg,
                diag=local_diag,
                use_cache=use_cache,
            )
            cells = verify_cells(cells, routed.page, diag=local_diag)
            return {
                "index": index,
                "url": page.url,
                "cells": cells,
                "scores": raw_scores,
                "diag": local_diag,
            }
        except Exception as exc:
            return {
                "index": index,
                "url": page.url,
                "cells": [],
                "scores": raw_scores,
                "diag": local_diag,
                "error": str(exc),
            }

    results: list[dict | None] = [None for _ in page_docs]
    max_workers = min(EXTRACT_PAGE_WORKERS, len(page_docs)) if page_docs else 1
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(process_page, index, page): index for index, page in enumerate(page_docs)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = {
                    "index": index,
                    "url": page_docs[index].url,
                    "cells": [],
                    "scores": {},
                    "diag": {},
                    "error": str(exc),
                }

    for result in results:
        if not result:
            continue
        url = result["url"]
        cells = result["cells"]
        all_cells.extend(cells)
        per_url_cells[url].extend(cells)
        per_url_scores[url] = result.get("scores", {})
        if result.get("error"):
            per_url_errors[url] = result["error"]
        elif result.get("diag", {}).get("score_error"):
            per_url_errors[url] = f"score_error: {result['diag']['score_error']}"

    final_by_url: dict[str, list[tuple[ExtractedCell, Any]]] = defaultdict(list)
    for cell, ev in _aggregate_final_evidence(all_cells, entities):
        source_url = ev.source_url or cell.source_url
        final_by_url[source_url].append((cell, ev))

    usefulness_rows: list[dict] = []
    detail_rows: list[dict] = []
    for fp in pages:
        cells = per_url_cells.get(fp.url, [])
        evidence = [ev for cell in cells for ev in cell.evidence]
        final_items = final_by_url.get(fp.url, [])
        extracted_candidates = len(evidence)
        verified_facts = sum(1 for ev in evidence if ev.verified)
        final_contributions = len(final_items)
        contributed_questions = sorted({cell.column for cell, _ in final_items})
        contribution_rate = final_contributions / extracted_candidates if extracted_candidates else 0.0
        avg_relevance = _avg(list(per_url_scores.get(fp.url, {}).values()))
        avg_confidence = _avg([
            ev.verification_score
            for ev in evidence
            if isinstance(ev.verification_score, (int, float))
        ])

        usefulness_rows.append({
            "url": fp.url,
            "page_title": _page_title(fp.markdown or ""),
            "crawl_score": round(fp.crawl_score, 3),
            "depth": fp.depth,
            "fetch_status": fp.status,
            "extracted_candidates": extracted_candidates,
            "verified_facts": verified_facts,
            "final_matrix_contributions": final_contributions,
            "questions_contributed_to": "; ".join(contributed_questions),
            "contribution_rate": round(contribution_rate, 3),
            "average_question_relevance": round(avg_relevance, 3) if avg_relevance is not None else "",
            "average_verification_confidence": round(avg_confidence, 1) if avg_confidence is not None else "",
            "diagnostic_error": per_url_errors.get(fp.url, ""),
        })

        for cell, ev in final_items:
            detail_rows.append({
                "url": fp.url,
                "entity": cell.entity,
                "question": cell.column,
                "value": ev.value,
                "verified": ev.verified,
                "verification_score": ev.verification_score,
                "quote": ev.quote,
                "match_type": ev.match_type,
            })

    return usefulness_rows, detail_rows


def _ranking_rows(usefulness_rows: list[dict]) -> list[dict]:
    rows = []
    zero = [row for row in usefulness_rows if row["final_matrix_contributions"] == 0]
    for rank, row in enumerate(sorted(zero, key=lambda r: r["crawl_score"], reverse=True)[:20], start=1):
        rows.append({"ranking": "highest_scoring_zero_contributions", "rank": rank, **row})

    many = [row for row in usefulness_rows if row["final_matrix_contributions"] > 0]
    for rank, row in enumerate(
        sorted(many, key=lambda r: (r["crawl_score"], -r["final_matrix_contributions"]))[:20],
        start=1,
    ):
        rows.append({"ranking": "lowest_scoring_many_contributions", "rank": rank, **row})

    if usefulness_rows:
        max_contrib = max(row["final_matrix_contributions"] for row in usefulness_rows) or 1
        scored = []
        for row in usefulness_rows:
            norm_contrib = row["final_matrix_contributions"] / max_contrib
            scored.append((norm_contrib - row["crawl_score"], row))

        for rank, (surprise, row) in enumerate(
            sorted(scored, key=lambda item: item[0], reverse=True)[:15],
            start=1,
        ):
            rows.append({
                "ranking": "unexpectedly_high_usefulness_vs_score",
                "rank": rank,
                "surprise": round(surprise, 3),
                **row,
            })
        for rank, (surprise, row) in enumerate(sorted(scored, key=lambda item: item[0])[:15], start=1):
            rows.append({
                "ranking": "unexpectedly_low_usefulness_vs_score",
                "rank": rank,
                "surprise": round(surprise, 3),
                **row,
            })

    return rows


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _paired_values(rows: list[dict], x_key: str, y_key: str) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        x = _number(row.get(x_key))
        y = _number(row.get(y_key))
        if x is None or y is None:
            continue
        xs.append(x)
        ys.append(y)
    return xs, ys


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denom_x = sum(v * v for v in dx)
    denom_y = sum(v * v for v in dy)
    if denom_x <= 0 or denom_y <= 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / math.sqrt(denom_x * denom_y)


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0 for _ in values]
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def _correlation_rows(usefulness_rows: list[dict]) -> list[dict]:
    pairs = [
        ("crawl_score", "extracted_candidates"),
        ("crawl_score", "verified_facts"),
        ("crawl_score", "final_matrix_contributions"),
        ("depth", "final_matrix_contributions"),
    ]
    rows = []
    for x_key, y_key in pairs:
        xs, ys = _paired_values(usefulness_rows, x_key, y_key)
        pearson = _pearson(xs, ys)
        spearman = _spearman(xs, ys)
        rows.append({
            "x_metric": x_key,
            "y_metric": y_key,
            "sample_size": len(xs),
            "pearson": round(pearson, 4) if pearson is not None else "",
            "spearman": round(spearman, 4) if spearman is not None else "",
            "status": "ok" if pearson is not None or spearman is not None else "insufficient_variation_or_sample",
        })
    return rows


def _score_bin(score: float) -> str:
    bins = [
        (0.0, 0.2, "0.0-0.2"),
        (0.2, 0.4, "0.2-0.4"),
        (0.4, 0.6, "0.4-0.6"),
        (0.6, 0.8, "0.6-0.8"),
        (0.8, 1.0000001, "0.8-1.0"),
    ]
    for low, high, label in bins:
        if low <= score < high:
            return label
    if score < 0.0:
        return "<0.0"
    return ">1.0"


def _calibration_rows(usefulness_rows: list[dict]) -> list[dict]:
    labels = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
    grouped: dict[str, list[dict]] = {label: [] for label in labels}
    for row in usefulness_rows:
        score = _number(row.get("crawl_score"))
        if score is None:
            continue
        label = _score_bin(score)
        grouped.setdefault(label, []).append(row)

    out = []
    for label in labels:
        rows = grouped.get(label, [])
        out.append({
            "crawl_score_bin": label,
            "page_count": len(rows),
            "avg_extracted_candidates": round(_avg([r["extracted_candidates"] for r in rows]) or 0.0, 3),
            "avg_verified_facts": round(_avg([r["verified_facts"] for r in rows]) or 0.0, 3),
            "avg_contributions": round(_avg([r["final_matrix_contributions"] for r in rows]) or 0.0, 3),
            "avg_contribution_rate": round(_avg([r["contribution_rate"] for r in rows]) or 0.0, 3),
        })
    return out


def _false_positive_rows(usefulness_rows: list[dict]) -> list[dict]:
    if not usefulness_rows:
        return []
    avg_contrib = _avg([row["final_matrix_contributions"] for row in usefulness_rows]) or 0.0
    rows = [
        row for row in usefulness_rows
        if row["crawl_score"] >= 0.6 and row["final_matrix_contributions"] <= avg_contrib
    ]
    return sorted(
        rows,
        key=lambda row: (-row["crawl_score"], row["final_matrix_contributions"], row["extracted_candidates"]),
    )


def _false_negative_rows(usefulness_rows: list[dict]) -> list[dict]:
    if not usefulness_rows:
        return []
    avg_contrib = _avg([row["final_matrix_contributions"] for row in usefulness_rows]) or 0.0
    avg_rate = _avg([row["contribution_rate"] for row in usefulness_rows]) or 0.0
    rows = [
        row for row in usefulness_rows
        if row["crawl_score"] <= 0.4
        and (
            row["final_matrix_contributions"] > avg_contrib
            or row["contribution_rate"] > avg_rate
        )
    ]
    return sorted(rows, key=lambda row: (-row["contribution_rate"], row["crawl_score"]))


def _interpret_correlation(value: float | None) -> str:
    if value is None:
        return "insufficient data to assess crawl-score predictiveness"
    abs_value = abs(value)
    if value > 0 and abs_value >= 0.7:
        return "crawl score is strongly predictive of downstream usefulness"
    if value > 0 and abs_value >= 0.4:
        return "crawl score is moderately predictive of downstream usefulness"
    if abs_value < 0.3:
        return "crawl score shows weak relationship with downstream contribution"
    if value < 0:
        return "crawl score is inversely related to downstream contribution in this run"
    return "crawl score shows limited predictive value for downstream usefulness"


def _evaluation_summary_rows(
    usefulness_rows: list[dict],
    correlation_rows: list[dict],
    false_positive_rows: list[dict],
    false_negative_rows: list[dict],
) -> list[dict]:
    correlations = []
    for row in correlation_rows:
        for metric in ("pearson", "spearman"):
            value = _number(row.get(metric))
            if value is not None:
                correlations.append({
                    "label": f"{metric}: {row['x_metric']} vs {row['y_metric']}",
                    "value": value,
                })

    positive = [row for row in correlations if row["value"] > 0]
    strongest = max(positive, key=lambda row: row["value"], default=None)
    weakest = min(correlations, key=lambda row: abs(row["value"]), default=None)
    primary_x, primary_y = _paired_values(usefulness_rows, "crawl_score", "final_matrix_contributions")
    primary_corr = _spearman(primary_x, primary_y)

    return [
        {"metric": "total_pages_analysed", "value": len(usefulness_rows)},
        {"metric": "average_crawl_score", "value": round(_avg([r["crawl_score"] for r in usefulness_rows]) or 0.0, 3)},
        {"metric": "average_contributions", "value": round(_avg([r["final_matrix_contributions"] for r in usefulness_rows]) or 0.0, 3)},
        {
            "metric": "strongest_positive_correlation",
            "value": "" if strongest is None else f"{strongest['label']} = {strongest['value']:.4f}",
        },
        {
            "metric": "weakest_correlation",
            "value": "" if weakest is None else f"{weakest['label']} = {weakest['value']:.4f}",
        },
        {"metric": "high_score_zero_or_low_contribution_pages", "value": len(false_positive_rows)},
        {"metric": "low_score_high_contribution_pages", "value": len(false_negative_rows)},
        {"metric": "interpretation", "value": _interpret_correlation(primary_corr)},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Acquire-layer diagnostic")
    parser.add_argument("input", help="Path to the input Excel file")
    parser.add_argument("--output", default="", help="Path to save the report Excel (optional)")
    parser.add_argument("--backend", default="", help="Override ACQUIRE_TOOL (local/firecrawl/playwright/requests)")
    parser.add_argument(
        "--scorer",
        choices=["baseline", "experimental"],
        default="",
        help=f"Crawl scoring mode override (default: workbook CRAWL_SCORER or {CRAWL_SCORER})",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="Override CRAWL_MAX_PAGES")
    parser.add_argument("--no-crawl", action="store_true", help="Force depth=0 for all URLs (no crawling)")
    parser.add_argument(
        "--no-usefulness",
        action="store_true",
        help="Skip downstream extract/verify/aggregate usefulness analysis",
    )
    parser.add_argument("--no-extract-cache", action="store_true", help="Bypass cached extraction responses")
    args = parser.parse_args()

    pipeline_input = read_input(args.input)
    crawl_scorer = (
        args.scorer
        or str(pipeline_input.config_overrides.get("CRAWL_SCORER", CRAWL_SCORER)).strip().lower()
    )

    cfg = Config(
        acquire_tool=args.backend or ACQUIRE_TOOL,
        extract_tool=EXTRACT_TOOL,
        cache_dir=CACHE_DIR,
        request_headers=REQUEST_HEADERS,
        default_depth=DEFAULT_DEPTH,
        crawl_min_score=CRAWL_MIN_SCORE,
        crawl_min_score_embed=CRAWL_MIN_SCORE_EMBED,
        crawl_max_pages=args.max_pages or CRAWL_MAX_PAGES,
        crawl_scorer=crawl_scorer,
    )

    print(f"\n{'='*72}")
    print(f"  ACQUIRE DIAGNOSTIC")
    print(f"  input    : {args.input}")
    print(f"  backend  : {cfg.acquire_tool}")
    print(f"  scorer   : {cfg.crawl_scorer}")
    print(f"  entities : {', '.join(pipeline_input.entities)}")
    print(f"  urls     : {len(pipeline_input.urls)}")
    print(f"  max pages: {cfg.crawl_max_pages}")
    print(f"{'='*72}\n")

    diag: dict = {}
    t0 = time.time()

    url_tuples = []
    for spec in pipeline_input.urls:
        depth = 0 if args.no_crawl else spec.depth
        url_tuples.append((spec.url, depth))

    pages: list[FetchedPage] = acquire(
        url_tuples,
        cfg,
        columns=pipeline_input.columns,
        entities=pipeline_input.entities,
        diag=diag,
    )

    elapsed = time.time() - t0

    # ── Per-page table ────────────────────────────────────────────────────────

    print(f"\n{'─'*72}")
    print(f"  {'ST':<3} {'BACKEND':<16} {'D':<2} {'SCORE':<6} {'CHARS':<7} {'MS':<6}  URL")
    print(f"{'─'*72}")

    rows = []
    for p in pages:
        icon = _status_icon(p.status)
        score = f"{p.crawl_score:.2f}" if p.depth > 0 else "seed"
        chars = len(p.markdown) if p.markdown else 0
        gate = "" if p.gate_passed is not False else f"  [{p.gate_reason}]"
        print(f"  {icon:<3} {p.backend:<16} {p.depth:<2} {score:<6} {chars:<7} {p.fetch_time_ms:<6}  {p.url}{gate}")

        rows.append({
            "status": p.status,
            "backend": p.backend,
            "crawl_scorer": cfg.crawl_scorer,
            "depth": p.depth,
            "crawl_score": round(p.crawl_score, 3),
            "chars": chars,
            "fetch_time_ms": p.fetch_time_ms,
            "gate_passed": p.gate_passed,
            "gate_reason": p.gate_reason,
            "render_fallback": p.render_fallback,
            "url": p.url,
            "content_preview": _preview(p.markdown or ""),
        })

    print(f"{'─'*72}\n")

    # ── Summary ───────────────────────────────────────────────────────────────

    total = len(pages)
    by_status = {}
    for p in pages:
        by_status[p.status] = by_status.get(p.status, 0) + 1
    by_backend = {}
    for p in pages:
        by_backend[p.backend] = by_backend.get(p.backend, 0) + 1

    print(f"  SUMMARY  ({total} pages in {elapsed:.1f}s)")
    print()
    for status, count in sorted(by_status.items()):
        icon = _status_icon(status)
        print(f"    {icon} {status:<14} {count:>3}  {_bar(count, total)}")
    print()
    print(f"  Backends used:")
    for backend, count in sorted(by_backend.items(), key=lambda x: -x[1]):
        print(f"    {backend:<18} {count:>3} pages")

    chars_ok = [len(p.markdown) for p in pages if p.markdown and p.status in ("ok", "cached")]
    if chars_ok:
        avg = sum(chars_ok) / len(chars_ok)
        print(f"\n  Avg content (ok/cached): {avg:,.0f} chars  "
              f"min={min(chars_ok):,}  max={max(chars_ok):,}")

    print()

    # ── Filter routing ────────────────────────────────────────────────────────

    filter_rows = []
    if pipeline_input.columns:
        print(f"  FILTER ROUTING  (threshold={FILTER_THRESHOLD})")
        print()
        try:
            filter_results = _filter_with_scores(pages, pipeline_input.columns, FILTER_THRESHOLD)
            total_cols = len(pipeline_input.columns)

            for fr in filter_results:
                n_relevant = len(fr["relevant"])
                fallback_note = "  [fallback: all]" if fr["fallback"] else ""
                print(f"  {fr['url']}")
                print(f"    {n_relevant}/{total_cols} relevant{fallback_note}")
                for name, score in fr["scores"].items():
                    tick = "✓" if name in fr["relevant"] else "✗"
                    score_str = f"{score:.3f}" if score is not None else "n/a "
                    print(f"    {tick} {score_str}  {name}")
                print()

                for name, score in fr["scores"].items():
                    filter_rows.append({
                        "url": fr["url"],
                        "depth": fr.get("depth", ""),
                        "column": name,
                        "score": score,
                        "above_threshold": (score is not None and score >= FILTER_THRESHOLD),
                        "keyword_gate": fr.get("keyword_gate", {}).get(name, False),
                        "relevant": name in fr["relevant"],
                        "fallback": fr["fallback"],
                    })

        except Exception as exc:
            print(f"  [filter] Ollama not reachable — skipping filter section ({exc})")
            print()
    else:
        print("  FILTER ROUTING  (skipped — no columns in input)")
        print()

    # ── Save report ───────────────────────────────────────────────────────────

    usefulness_rows = []
    contribution_rows = []
    ranking_rows = []
    correlation_rows = []
    calibration_rows = []
    false_positive_rows = []
    false_negative_rows = []
    evaluation_summary_rows = []
    if args.no_usefulness:
        print("  PAGE USEFULNESS  (skipped by --no-usefulness)")
        print()
    elif not pipeline_input.columns or not pipeline_input.entities:
        print("  PAGE USEFULNESS  (skipped - requires columns and entities)")
        print()
    else:
        print("  PAGE USEFULNESS  (filter + extract + verify + aggregate)")
        t_use = time.time()
        usefulness_rows, contribution_rows = _build_usefulness_rows(
            pages,
            pipeline_input.columns,
            pipeline_input.entities,
            cfg,
            use_cache=not args.no_extract_cache,
        )
        ranking_rows = _ranking_rows(usefulness_rows)
        correlation_rows = _correlation_rows(usefulness_rows)
        calibration_rows = _calibration_rows(usefulness_rows)
        false_positive_rows = _false_positive_rows(usefulness_rows)
        false_negative_rows = _false_negative_rows(usefulness_rows)
        evaluation_summary_rows = _evaluation_summary_rows(
            usefulness_rows,
            correlation_rows,
            false_positive_rows,
            false_negative_rows,
        )
        elapsed_use = time.time() - t_use
        contributing_pages = sum(1 for row in usefulness_rows if row["final_matrix_contributions"] > 0)
        total_contributions = sum(row["final_matrix_contributions"] for row in usefulness_rows)
        print(
            f"    {contributing_pages}/{len(usefulness_rows)} page(s) contributed "
            f"{total_contributions} final matrix item(s) in {elapsed_use:.1f}s"
        )
        interpretation = next(
            (row["value"] for row in evaluation_summary_rows if row["metric"] == "interpretation"),
            "",
        )
        if interpretation:
            print(f"    Interpretation: {interpretation}")
        print()

    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output_path = os.path.join("outputs", f"{base}_acquire_report.xlsx")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df_pages = pd.DataFrame(rows)

    crawl_rows = diag.get("crawl_candidates", [])
    df_crawl = pd.DataFrame(crawl_rows) if crawl_rows else pd.DataFrame(
        columns=[
            "parent_url", "candidate_url", "anchor_text", "url_path",
            "crawl_score", "crawl_scorer", "threshold", "followed", "skip_reason",
        ]
    )

    df_filter = pd.DataFrame(filter_rows) if filter_rows else pd.DataFrame(
        columns=["url", "depth", "column", "score", "above_threshold", "keyword_gate", "relevant", "fallback"]
    )

    df_usefulness = pd.DataFrame(usefulness_rows) if usefulness_rows else pd.DataFrame(
        columns=[
            "url", "page_title", "crawl_score", "depth", "fetch_status",
            "extracted_candidates", "verified_facts", "final_matrix_contributions",
            "questions_contributed_to", "contribution_rate",
            "average_question_relevance", "average_verification_confidence",
            "diagnostic_error",
        ]
    )
    df_rankings = pd.DataFrame(ranking_rows) if ranking_rows else pd.DataFrame(
        columns=[
            "ranking", "rank", "surprise", "url", "page_title", "crawl_score",
            "depth", "fetch_status", "extracted_candidates", "verified_facts",
            "final_matrix_contributions", "questions_contributed_to",
            "contribution_rate", "average_question_relevance",
            "average_verification_confidence", "diagnostic_error",
        ]
    )
    df_contrib = pd.DataFrame(contribution_rows) if contribution_rows else pd.DataFrame(
        columns=[
            "url", "entity", "question", "value", "verified",
            "verification_score", "quote", "match_type",
        ]
    )
    df_correlations = pd.DataFrame(correlation_rows) if correlation_rows else pd.DataFrame(
        columns=["x_metric", "y_metric", "sample_size", "pearson", "spearman", "status"]
    )
    df_calibration = pd.DataFrame(calibration_rows) if calibration_rows else pd.DataFrame(
        columns=[
            "crawl_score_bin", "page_count", "avg_extracted_candidates",
            "avg_verified_facts", "avg_contributions", "avg_contribution_rate",
        ]
    )
    diagnostic_cols = [
        "url", "page_title", "crawl_score", "depth", "extracted_candidates",
        "verified_facts", "final_matrix_contributions",
    ]
    df_false_positives = pd.DataFrame(false_positive_rows) if false_positive_rows else pd.DataFrame(
        columns=diagnostic_cols
    )
    df_false_positives = df_false_positives.reindex(columns=diagnostic_cols)
    df_false_negatives = pd.DataFrame(false_negative_rows) if false_negative_rows else pd.DataFrame(
        columns=diagnostic_cols
    )
    df_false_negatives = df_false_negatives.reindex(columns=diagnostic_cols)
    df_evaluation = pd.DataFrame(evaluation_summary_rows) if evaluation_summary_rows else pd.DataFrame(
        columns=["metric", "value"]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_pages.to_excel(writer, sheet_name="Pages", index=False)
        df_crawl.to_excel(writer, sheet_name="Crawl Candidates", index=False)
        df_filter.to_excel(writer, sheet_name="Filter", index=False)
        df_evaluation.to_excel(writer, sheet_name="Crawl Score Evaluation", index=False)
        df_usefulness.to_excel(writer, sheet_name="Page Usefulness", index=False)
        df_correlations.to_excel(writer, sheet_name="Score Correlations", index=False)
        df_calibration.to_excel(writer, sheet_name="Score Calibration", index=False)
        df_false_positives.to_excel(writer, sheet_name="False Positives", index=False)
        df_false_negatives.to_excel(writer, sheet_name="False Negatives", index=False)
        df_rankings.to_excel(writer, sheet_name="Usefulness Rankings", index=False)
        df_contrib.to_excel(writer, sheet_name="Contribution Details", index=False)

    print(f"  Report saved → {output_path}\n")


if __name__ == "__main__":
    main()
