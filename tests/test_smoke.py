"""
Smoke tests for entity extraction pipeline.

Tests:
- Parser handles dict/list/scalar/null output.
- Aggregator groups by entity and question.
- Excel output uses entity rows and provenance entity/source URL columns.
- New four-sheet input workbook is parsed correctly.
- Blank URL entities fan out to all entities.
- Specific URL entities stay scoped to those entities.
- Backward compatibility treats URL as entity when entities sheet is missing.
- main.py only prompts for input path and output filename.
"""

import os
import re
import tempfile
from pathlib import Path

import pandas as pd

import src.extract as extract
from src.aggregate import aggregate_cells
from src.extract import _merge_chunk_data, _parse_field_value
from src.io_excel import read_input, write_output_excel
from models import ColumnSpec, Config, ExtractedCell, ExtractedRow, PageDoc, PipelineResult, SourceQuote
from src.acquire.crawler import build_crawl_query
from src.acquire.link_scorer import score_links
from src.acquire.acquire_models import LinkCandidate


def test_parse_dict_output():
    raw = {
        "value": "test value",
        "quote": "test quote from page",
    }

    value, evidence = _parse_field_value(raw)

    assert value == "test value"
    assert len(evidence) == 1
    assert evidence[0].value == "test value"
    assert evidence[0].quote == "test quote from page"
    print("OK test_parse_dict_output passed")


def test_parse_list_of_dicts():
    raw = [
        {"value": "item 1", "quote": "quote 1"},
        {"value": "item 2", "quote": "quote 2"},
    ]

    value, evidence = _parse_field_value(raw)

    assert isinstance(value, list)
    assert len(value) == 2
    assert len(evidence) == 2
    assert evidence[0].value == "item 1"
    assert evidence[1].value == "item 2"
    print("OK test_parse_list_of_dicts passed")


def test_parse_scalar_output():
    raw = "plain text value"

    value, evidence = _parse_field_value(raw)

    assert value == "plain text value"
    assert len(evidence) == 1
    assert evidence[0].value == "plain text value"
    assert evidence[0].quote is None
    print("OK test_parse_scalar_output passed")


def test_parse_null_output():
    raw = None

    value, evidence = _parse_field_value(raw)

    assert value is None
    assert len(evidence) == 0
    print("OK test_parse_null_output passed")


def test_merge_chunk_data_recursively_flattens_nested_values():
    chunk_results = [
        {
            "Oatly": {
                "Ingredients": [
                    "oat",
                    ["oat"],
                    [[{"value": "oats", "quote": "made with oats"}]],
                    {"value": ["barley", ["barley"]], "quote": "contains barley"},
                ]
            }
        }
    ]

    merged = _merge_chunk_data(chunk_results)

    assert merged == {
        "Oatly": {
            "Ingredients": [
                {"value": "oat", "quote": None},
                {"value": "oats", "quote": "made with oats"},
                {"value": "barley", "quote": "contains barley"},
            ]
        }
    }
    print("OK test_merge_chunk_data_recursively_flattens_nested_values passed")


def test_aggregator_groups_by_entity_and_question():
    cells = [
        ExtractedCell(
            entity="Oatly",
            source_url="http://example.com/oatly",
            column="Sustainability claims",
            value="oat claim",
            evidence=[SourceQuote(value="oat claim", quote="quote")],
            verified=True,
            verification_score=90.0,
        ),
        ExtractedCell(
            entity="Ripple",
            source_url="http://example.com/ripple",
            column="Sustainability claims",
            value="pea claim",
            evidence=[SourceQuote(value="pea claim", quote="quote")],
            verified=True,
            verification_score=90.0,
        ),
    ]

    aggregated = aggregate_cells(cells)

    assert len(aggregated) == 2
    assert {cell.entity for cell in aggregated} == {"Oatly", "Ripple"}
    print("OK test_aggregator_groups_by_entity_and_question passed")


def test_aggregator_merges_contributions_without_winner_selection():
    cells = [
        ExtractedCell(
            entity="Oatly",
            source_url="http://example.com/1",
            column="test_col",
            value="unverified value",
            evidence=[SourceQuote(value="unverified value", quote="quote 1")],
            verified=False,
            verification_score=40.0,
        ),
        ExtractedCell(
            entity="Oatly",
            source_url="http://example.com/2",
            column="test_col",
            value="verified value",
            evidence=[SourceQuote(value="verified value", quote="quote 2", verified=True, verification_score=85.0)],
            verified=True,
            verification_score=85.0,
        ),
    ]

    aggregated = aggregate_cells(cells)

    assert len(aggregated) == 1
    assert aggregated[0].value == ["unverified value", "verified value"]
    assert aggregated[0].verified is False
    assert aggregated[0].has_conflict is True
    assert aggregated[0].num_sources == 2
    assert aggregated[0].num_unique_values == 2
    assert {ev.source_url for ev in aggregated[0].evidence} == {
        "http://example.com/1",
        "http://example.com/2",
    }
    print("OK test_aggregator_merges_contributions_without_winner_selection passed")


def test_aggregator_dedupes_by_value_quote_and_source_url():
    cells = [
        ExtractedCell(
            entity="Silk",
            source_url="http://example.com/a",
            column="Parent companies",
            value="Danone North America",
            evidence=[SourceQuote(value="Danone North America", quote="owned by Danone")],
        ),
        ExtractedCell(
            entity="Silk",
            source_url="http://example.com/a",
            column="Parent companies",
            value="danone   north america",
            evidence=[SourceQuote(value="danone   north america", quote="owned by Danone")],
        ),
        ExtractedCell(
            entity="Silk",
            source_url="http://example.com/b",
            column="Parent companies",
            value="Danone North America",
            evidence=[SourceQuote(value="Danone North America", quote="owned by Danone")],
        ),
    ]

    aggregated = aggregate_cells(cells)

    assert len(aggregated) == 1
    assert aggregated[0].value == ["Danone North America"]
    assert len(aggregated[0].evidence) == 2
    assert aggregated[0].has_conflict is False
    assert aggregated[0].num_sources == 2
    assert aggregated[0].num_unique_values == 1
    print("OK test_aggregator_dedupes_by_value_quote_and_source_url passed")


def test_excel_output_uses_entity_rows_and_provenance_entity():
    columns = [ColumnSpec(name="test_col")]

    result = PipelineResult(
        rows=[
            ExtractedRow(
                entity="Oatly",
                cells=[
                    ExtractedCell(
                        entity="Oatly",
                        source_url="http://example.com/page1",
                        column="test_col",
                        value=["item 1", "item 2"],
                        evidence=[
                            SourceQuote(value="item 1", quote="quote 1"),
                            SourceQuote(value="item 2", quote="quote 2"),
                        ],
                        verified=True,
                    ),
                ],
            ),
        ]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test_output.xlsx")
        write_output_excel(result, columns, output_path)

        matrix_df = pd.read_excel(output_path, sheet_name="Matrix")
        provenance_df = pd.read_excel(output_path, sheet_name="Provenance")

        assert matrix_df.columns[0] == "Entity"
        assert matrix_df.iloc[0]["Entity"] == "Oatly"
        assert len(provenance_df) == 2
        assert provenance_df.iloc[0]["Entity"] == "Oatly"
        assert provenance_df.iloc[0]["Source URL"] == "http://example.com/page1"
        assert provenance_df.iloc[0]["Claim"] == "item 1"
        assert provenance_df.iloc[1]["Claim"] == "item 2"
        assert provenance_df.iloc[0]["Verbatim Quote"] == "quote 1"

    print("OK test_excel_output_uses_entity_rows_and_provenance_entity passed")


def test_sample_input_populates_entities_urls_questions_and_config():
    data = read_input("samples/test_smoke.xlsx")

    assert data.entities == ["Oatly", "Ripple", "Califia", "Silk", "Elmhurst"]
    assert len(data.urls) == 6
    assert len(data.columns) == 3
    assert data.config_overrides == {"CRAWL_MAX_PAGES": 15, "DEFAULT_DEPTH": 1}
    assert data.columns[0].name == "What sustainability claims does the brand make?"
    assert data.columns[0].instruction == "return as a list, one claim per item"
    print("OK test_sample_input_populates_entities_urls_questions_and_config passed")


def test_blank_entities_url_gets_all_entities_and_specific_url_stays_scoped():
    data = read_input("samples/test_smoke.xlsx")

    assert data.urls[0].entities == ["Oatly"]
    assert data.urls[-1].url == "https://www.mintel.com/food-and-drink/plant-based-milk"
    assert data.urls[-1].entities == data.entities
    print("OK test_blank_entities_url_gets_all_entities_and_specific_url_stays_scoped passed")


def test_extract_cells_only_returns_requested_entities():
    columns = [ColumnSpec(name="Question")]
    page = PageDoc(url="http://example.com", text="Oatly claim. Ripple claim.")
    original = extract._extract_with_sgai

    def fake_extract(page, columns, entities):
        return {
            "Oatly": {"Question": {"value": "Oatly claim", "quote": "Oatly claim"}},
            "Ripple": {"Question": {"value": "Ripple claim", "quote": "Ripple claim"}},
        }, {"extraction_time_ms": 1, "timed_out": False, "retry_count": 0}

    try:
        extract._extract_with_sgai = fake_extract
        cells = extract.extract_cells(
            page,
            columns,
            entities=["Oatly"],
            cfg=Config(extract_tool="sgai"),
        )
    finally:
        extract._extract_with_sgai = original

    assert len(cells) == 1
    assert cells[0].entity == "Oatly"
    assert cells[0].value == "Oatly claim"
    print("OK test_extract_cells_only_returns_requested_entities passed")


def test_backward_compatibility_without_entities_sheet():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "legacy.xlsx")
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame({
                "url": ["http://example.com/a", "http://example.com/b"],
                "depth": [0, 1],
            }).to_excel(writer, sheet_name="urls", index=False)
            pd.DataFrame({
                "question": ["Question"],
            }).to_excel(writer, sheet_name="questions", index=False)

        data = read_input(path)

    assert data.entities == ["http://example.com/a", "http://example.com/b"]
    assert data.urls[0].entities == ["http://example.com/a"]
    assert data.urls[1].entities == ["http://example.com/b"]
    print("OK test_backward_compatibility_without_entities_sheet passed")


def test_main_only_two_terminal_prompts():
    main_text = Path("main.py").read_text(encoding="utf-8")

    assert len(re.findall(r"(?<![A-Za-z0-9_])input\(", main_text)) == 2
    assert "get_columns_from_user" not in main_text
    print("OK test_main_only_two_terminal_prompts passed")


def test_crawl_terms_include_questions_and_instructions_only():
    columns = [
        ColumnSpec(
            name="What is the warranty policy?",
            instruction="include warranty period and coverage limits",
        )
    ]

    terms = build_crawl_query(columns, entities=["Acme-Tools"])

    assert "warranty" in terms
    assert "policy" in terms
    assert "period" in terms
    assert "coverage" in terms
    assert "limits" in terms
    assert "acme" not in terms
    assert "tools" not in terms
    assert "company" not in terms
    assert "overview" not in terms
    assert "products" not in terms
    print("OK test_crawl_terms_include_questions_and_instructions_only passed")


def test_link_scorer_has_no_domain_specific_keyword_boosts():
    candidates = [
        LinkCandidate(
            url="https://example.com/sustainability",
            anchor_text="Sustainability",
            depth=1,
        ),
        LinkCandidate(
            url="https://example.com/warranty",
            anchor_text="Warranty policy",
            depth=1,
        ),
    ]

    scored = score_links(candidates, {"warranty": 3.0, "policy": 3.0, "acme": 2.0})

    by_url = {candidate.url: candidate.score for candidate in scored}
    assert by_url["https://example.com/sustainability"] == 0.0
    assert by_url["https://example.com/warranty"] == 1.0
    print("OK test_link_scorer_has_no_domain_specific_keyword_boosts passed")


if __name__ == "__main__":
    test_parse_dict_output()
    test_parse_list_of_dicts()
    test_parse_scalar_output()
    test_parse_null_output()
    test_merge_chunk_data_recursively_flattens_nested_values()
    test_aggregator_groups_by_entity_and_question()
    test_aggregator_merges_contributions_without_winner_selection()
    test_aggregator_dedupes_by_value_quote_and_source_url()
    test_excel_output_uses_entity_rows_and_provenance_entity()
    test_sample_input_populates_entities_urls_questions_and_config()
    test_blank_entities_url_gets_all_entities_and_specific_url_stays_scoped()
    test_extract_cells_only_returns_requested_entities()
    test_backward_compatibility_without_entities_sheet()
    test_main_only_two_terminal_prompts()
    test_crawl_terms_include_questions_and_instructions_only()
    test_link_scorer_has_no_domain_specific_keyword_boosts()

    print("\nAll smoke tests passed!")
