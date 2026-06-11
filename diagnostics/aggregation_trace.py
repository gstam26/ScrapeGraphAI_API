"""
Aggregation diagnostic trace.

Runs the pipeline, inspects raw pre-aggregation contributions, and writes
diagnostics that explain noisy, duplicated, or overloaded entity/question cells.

Usage:
    python diagnostics/aggregation_trace.py samples/test_smoke.xlsx
    python diagnostics/aggregation_trace.py samples/test_smoke.xlsx --backend firecrawl
    python diagnostics/aggregation_trace.py samples/test_smoke.xlsx --output outputs/agg_trace.xlsx
    python diagnostics/aggregation_trace.py samples/test_smoke.xlsx --json-output outputs/agg_trace.json
"""

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd
from dotenv import load_dotenv
from rapidfuzz import fuzz

load_dotenv()

from config import (
    AGGREGATION_BOILERPLATE_TERMS,
    AGGREGATION_LOW_RELEVANCE_THRESHOLD,
    AGGREGATION_NEAR_DUPLICATE_THRESHOLD,
)
from models import ExtractedCell, PipelineInput
from pipeline import run_pipeline
from src.io_excel import read_input


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_text(value: Any, boilerplate_terms: set[str]) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not boilerplate_terms:
        return text
    tokens = [token for token in text.split() if token not in boilerplate_terms]
    return " ".join(tokens)


def _parse_terms(raw: str) -> set[str]:
    if not raw:
        return set()
    return {
        token.strip().lower()
        for token in raw.split(",")
        if token.strip()
    }


def _score_text(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a, b))


def _confidence(cell: ExtractedCell, evidence) -> float | None:
    for value in (
        evidence.confidence_score,
        evidence.semantic_score,
        evidence.verification_score,
        cell.verification_score,
    ):
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _diag_lookup(diag: dict) -> dict[tuple[str, str, str], str]:
    lookup: dict[tuple[str, str, str], str] = {}
    for row in diag.get("extract_log", []):
        key = (
            _clean_text(row.get("entity")),
            _clean_text(row.get("source_url")),
            _clean_text(row.get("question")),
        )
        lookup[key] = _clean_text(row.get("extract_tool"))
    return lookup


def _find_extract_method(cell: ExtractedCell, question: str, evidence, extract_lookup: dict) -> str:
    if evidence.extraction_method:
        return evidence.extraction_method
    key = (cell.entity, evidence.source_url or cell.source_url, question)
    return extract_lookup.get(key, "")


def _contribution_rows(
    pipeline_input: PipelineInput,
    rows,
    diag: dict,
    boilerplate_terms: set[str],
    low_relevance_threshold: float,
) -> list[dict]:
    question_by_name = {column.name: column for column in pipeline_input.columns}
    extract_lookup = _diag_lookup(diag)
    contributions: list[dict] = []

    for entity_row in rows:
        for cell in entity_row.all_cells:
            question = cell.column
            question_spec = question_by_name.get(question)
            question_text = question
            if question_spec and question_spec.instruction:
                question_text = f"{question} {question_spec.instruction}"
            norm_question = _normalise_text(question_text, boilerplate_terms)

            for evidence in cell.evidence:
                value = _clean_text(evidence.value)
                quote = _clean_text(evidence.quote)
                source_url = _clean_text(evidence.source_url or cell.source_url)
                page_title = _clean_text(evidence.page_title)
                extraction_method = _find_extract_method(cell, question, evidence, extract_lookup)
                surrounding_text = quote

                norm_value = _normalise_text(value, boilerplate_terms)
                component_scores = {
                    "value": _score_text(norm_question, norm_value),
                    "quote": _score_text(norm_question, _normalise_text(quote, boilerplate_terms)),
                    "source_url": _score_text(norm_question, _normalise_text(source_url, boilerplate_terms)),
                    "page_title": _score_text(norm_question, _normalise_text(page_title, boilerplate_terms)),
                    "surrounding_text": _score_text(norm_question, _normalise_text(surrounding_text, boilerplate_terms)),
                }
                relevance_score = max(component_scores.values(), default=0.0)

                contributions.append({
                    "entity": entity_row.entity,
                    "question": question,
                    "value": value,
                    "normalized_value": norm_value,
                    "quote": quote,
                    "source_url": source_url,
                    "page_title": page_title,
                    "extraction_method": extraction_method,
                    "confidence": _confidence(cell, evidence),
                    "verified": evidence.verified,
                    "match_type": evidence.match_type,
                    "question_relevance_score": round(relevance_score, 1),
                    "low_question_relevance": relevance_score < low_relevance_threshold,
                    "value_relevance_score": round(component_scores["value"], 1),
                    "quote_relevance_score": round(component_scores["quote"], 1),
                    "source_url_relevance_score": round(component_scores["source_url"], 1),
                    "page_title_relevance_score": round(component_scores["page_title"], 1),
                    "surrounding_text_relevance_score": round(component_scores["surrounding_text"], 1),
                })

    return contributions


def _near_duplicate_clusters(values: list[str], threshold: float) -> list[list[str]]:
    clusters: list[list[str]] = []
    for value in values:
        if not value:
            continue
        placed = False
        for cluster in clusters:
            if any(_score_text(value, existing) >= threshold for existing in cluster):
                cluster.append(value)
                placed = True
                break
        if not placed:
            clusters.append([value])
    return clusters


def _counter_preview(counter: Counter, limit: int = 5) -> str:
    return "; ".join(f"{key} ({count})" for key, count in counter.most_common(limit) if key)


def _examples(items: list[str], limit: int = 3) -> str:
    seen: set[str] = set()
    picked: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        picked.append(item)
        if len(picked) >= limit:
            break
    return " | ".join(picked)


def _confidence_distribution(values: list[float]) -> str:
    if not values:
        return ""
    values_sorted = sorted(values)
    median = statistics.median(values_sorted)
    return (
        f"min={min(values_sorted):.1f}; "
        f"median={median:.1f}; "
        f"max={max(values_sorted):.1f}"
    )


def _recommended_action(
    duplicate_ratio: float,
    low_relevance_count: int,
    conflict_count: int,
    raw_count: int,
) -> str:
    if raw_count == 0:
        return "no_action_no_contributions"
    actions = []
    if low_relevance_count:
        actions.append("inspect_filter_or_extraction_prompt_for_question_relevance")
    if duplicate_ratio >= 0.4:
        actions.append("consider_stronger_deduplication_or_value_normalisation")
    if conflict_count:
        actions.append("defer_to_ranking_or_human_review_for_conflicting_values")
    if raw_count >= 10:
        actions.append("consider_cell_summarisation_or_result_limit")
    return "; ".join(actions) if actions else "keep_current_collection"


def _summary_rows(contributions: list[dict], near_threshold: float) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in contributions:
        grouped[(row["entity"], row["question"])].append(row)

    summaries: list[dict] = []
    for (entity, question), rows in grouped.items():
        raw_count = len(rows)
        normalized_values = [row["normalized_value"] for row in rows if row["normalized_value"]]
        exact_counter = Counter(normalized_values)
        exact_unique_count = len(exact_counter)
        clusters = _near_duplicate_clusters(list(exact_counter), near_threshold)
        near_duplicate_cluster_count = len(clusters)
        duplicate_count = raw_count - exact_unique_count
        duplicate_ratio = duplicate_count / raw_count if raw_count else 0.0
        source_counter = Counter(row["source_url"] for row in rows if row["source_url"])
        title_counter = Counter(row["page_title"] for row in rows if row["page_title"])
        method_counter = Counter(row["extraction_method"] for row in rows if row["extraction_method"])
        confidence_values = [
            float(row["confidence"])
            for row in rows
            if isinstance(row.get("confidence"), (int, float))
        ]
        low_relevance_rows = [row for row in rows if row["low_question_relevance"]]
        duplicate_values = [value for value, count in exact_counter.items() if count > 1]
        near_duplicate_examples = [
            " / ".join(cluster[:3])
            for cluster in clusters
            if len(cluster) > 1
        ]
        conflict_count = max(near_duplicate_cluster_count - 1, 0)

        summaries.append({
            "entity": entity,
            "question": question,
            "raw_contribution_count": raw_count,
            "exact_unique_count": exact_unique_count,
            "near_duplicate_cluster_count": near_duplicate_cluster_count,
            "source_count": len(source_counter),
            "duplicate_ratio": round(duplicate_ratio, 3),
            "low_relevance_count": len(low_relevance_rows),
            "conflict_count": conflict_count,
            "top_sources": _counter_preview(source_counter),
            "page_title_distribution": _counter_preview(title_counter),
            "extraction_method_distribution": _counter_preview(method_counter),
            "confidence_distribution": _confidence_distribution(confidence_values),
            "top_repeated_normalized_values": _counter_preview(exact_counter),
            "example_duplicates": _examples(duplicate_values),
            "example_near_duplicates": _examples(near_duplicate_examples),
            "example_low_relevance_values": _examples([row["value"] for row in low_relevance_rows]),
            "example_conflicting_values": _examples([cluster[0] for cluster in clusters], limit=5),
            "recommended_action": _recommended_action(
                duplicate_ratio,
                len(low_relevance_rows),
                conflict_count,
                raw_count,
            ),
        })

    return sorted(
        summaries,
        key=lambda row: (
            row["raw_contribution_count"],
            row["conflict_count"],
            row["duplicate_ratio"],
        ),
        reverse=True,
    )


def _write_outputs(summary_rows: list[dict], contribution_rows: list[dict], output_path: str, json_path: str) -> None:
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Aggregation Diagnostics", index=False)
            pd.DataFrame(contribution_rows).to_excel(writer, sheet_name="Contribution Details", index=False)

    if json_path:
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "aggregation_diagnostics": summary_rows,
                    "contribution_details": contribution_rows,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregation diagnostic trace")
    parser.add_argument("input", help="Path to input Excel file")
    parser.add_argument("--output", default="", help="Path to save diagnostics workbook")
    parser.add_argument("--json-output", default="", help="Optional JSON output path")
    parser.add_argument("--backend", default="firecrawl", help="Acquire backend (default: firecrawl)")
    parser.add_argument("--near-threshold", type=float, default=AGGREGATION_NEAR_DUPLICATE_THRESHOLD)
    parser.add_argument("--low-relevance-threshold", type=float, default=AGGREGATION_LOW_RELEVANCE_THRESHOLD)
    parser.add_argument(
        "--boilerplate-terms",
        default="",
        help=(
            "Comma-separated per-run terms to ignore during text normalization. "
            "Use for question/domain-specific diagnostics instead of editing config.py."
        ),
    )
    args = parser.parse_args()

    pipeline_input = read_input(args.input)
    pipeline_input.config_overrides = {
        **pipeline_input.config_overrides,
        "ACQUIRE_TOOL": args.backend,
    }

    result, diag = run_pipeline(pipeline_input)
    boilerplate_terms = set(AGGREGATION_BOILERPLATE_TERMS) | _parse_terms(args.boilerplate_terms)
    contribution_rows = _contribution_rows(
        pipeline_input,
        result.rows,
        diag,
        boilerplate_terms,
        args.low_relevance_threshold,
    )
    summary_rows = _summary_rows(contribution_rows, args.near_threshold)

    output_path = args.output
    if not output_path:
        base = os.path.splitext(os.path.basename(args.input))[0]
        output_path = os.path.join("outputs", f"{base}_aggregation_diagnostics.xlsx")

    _write_outputs(summary_rows, contribution_rows, output_path, args.json_output)

    print(f"\nAggregation diagnostics written to {output_path}")
    if args.json_output:
        print(f"JSON diagnostics written to {args.json_output}")
    print("\nMost overloaded cells:")
    for row in summary_rows[:10]:
        print(
            f"  {row['entity']} / {row['question']}: "
            f"{row['raw_contribution_count']} raw, "
            f"{row['exact_unique_count']} unique, "
            f"{row['near_duplicate_cluster_count']} clusters, "
            f"{row['low_relevance_count']} low relevance"
        )


if __name__ == "__main__":
    main()
