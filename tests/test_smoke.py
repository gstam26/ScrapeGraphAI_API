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
from src.aggregate import aggregate_cells, _is_list_column
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

    raw_cell = ExtractedCell(
        entity="Oatly",
        source_url="http://example.com/page1",
        column="test_col",
        value=["item 1", "item 2"],
        evidence=[
            SourceQuote(value="item 1", quote="quote 1"),
            SourceQuote(value="item 2", quote="quote 2"),
        ],
        verified=True,
    )

    result = PipelineResult(
        rows=[
            ExtractedRow(
                entity="Oatly",
                cells=[raw_cell],       # aggregated — Matrix reads this
                all_cells=[raw_cell],   # granular  — Provenance reads this
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


def test_matrix_reads_cells_not_all_cells():
    """Matrix must show data from row.cells (aggregated); row.all_cells is irrelevant to it."""
    columns = [ColumnSpec(name="Q")]

    agg_cell = ExtractedCell(
        entity="E",
        source_url="http://x.com",
        column="Q",
        value=["agg value"],
        evidence=[SourceQuote(value="agg value", quote="q", verified=True, match_type="exact")],
    )
    raw_cell = ExtractedCell(
        entity="E",
        source_url="http://x.com",
        column="Q",
        value=["raw value"],
        evidence=[SourceQuote(value="raw value", quote="q2")],
    )

    result = PipelineResult(
        rows=[ExtractedRow(entity="E", cells=[agg_cell], all_cells=[raw_cell])]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "out.xlsx")
        write_output_excel(result, columns, output_path)
        matrix_df = pd.read_excel(output_path, sheet_name="Matrix")

    cell_text = str(matrix_df.iloc[0]["Q"])
    assert "agg value" in cell_text, f"Matrix should show aggregated value, got: {cell_text!r}"
    assert "raw value" not in cell_text, f"Matrix must not show raw value, got: {cell_text!r}"
    print("OK test_matrix_reads_cells_not_all_cells passed")


def test_provenance_reads_all_cells_not_cells():
    """Provenance must stay granular on row.all_cells; row.cells is irrelevant to it."""
    columns = [ColumnSpec(name="Q")]

    agg_cell = ExtractedCell(
        entity="E",
        source_url="http://x.com",
        column="Q",
        value=["agg value"],
        evidence=[SourceQuote(value="agg value", quote="agg quote")],
    )
    raw_cell = ExtractedCell(
        entity="E",
        source_url="http://x.com",
        column="Q",
        value=["raw value"],
        evidence=[SourceQuote(value="raw value", quote="raw quote")],
    )

    result = PipelineResult(
        rows=[ExtractedRow(entity="E", cells=[agg_cell], all_cells=[raw_cell])]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "out.xlsx")
        write_output_excel(result, columns, output_path)
        prov_df = pd.read_excel(output_path, sheet_name="Provenance")

    assert len(prov_df) == 1
    assert prov_df.iloc[0]["Claim"] == "raw value"
    assert prov_df.iloc[0]["Verbatim Quote"] == "raw quote"
    print("OK test_provenance_reads_all_cells_not_cells passed")


def test_matrix_conflict_label():
    """When has_conflict is True the Matrix cell text starts with '(sources conflict)'."""
    columns = [ColumnSpec(name="Q")]

    conflict_cell = ExtractedCell(
        entity="E",
        source_url="http://a.com; http://b.com",
        column="Q",
        value=["value A", "value B"],
        evidence=[
            SourceQuote(value="value A", quote="qa", verified=True, match_type="exact"),
            SourceQuote(value="value B", quote="qb", verified=False, match_type="none"),
        ],
        has_conflict=True,
    )

    result = PipelineResult(
        rows=[ExtractedRow(entity="E", cells=[conflict_cell], all_cells=[])]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "out.xlsx")
        write_output_excel(result, columns, output_path)
        matrix_df = pd.read_excel(output_path, sheet_name="Matrix")

    cell_text = str(matrix_df.iloc[0]["Q"])
    assert cell_text.startswith("(sources conflict)"), f"Expected conflict prefix, got: {cell_text!r}"
    assert "value A" in cell_text
    assert "value B" in cell_text
    print("OK test_matrix_conflict_label passed")


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


def test_extract_cells_supports_claude_backend():
    columns = [ColumnSpec(name="Question")]
    page = PageDoc(url="http://example.com", text="Oatly claim.")
    original = extract._extract_with_claude

    def fake_extract(page, columns, entities):
        return {
            "Oatly": {"Question": {"value": "Oatly claim", "quote": "Oatly claim"}},
        }, {"extraction_time_ms": 1, "timed_out": False, "retry_count": 0}

    try:
        extract._extract_with_claude = fake_extract
        cells = extract.extract_cells(
            page,
            columns,
            entities=["Oatly"],
            cfg=Config(extract_tool="claude"),
            use_cache=False,
        )
    finally:
        extract._extract_with_claude = original

    assert len(cells) == 1
    assert cells[0].entity == "Oatly"
    assert cells[0].value == "Oatly claim"
    print("OK test_extract_cells_supports_claude_backend passed")


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


def test_rank_evidence_orders_exact_over_fuzzy_over_none():
    from src.aggregate import _rank_evidence
    evidence = [
        SourceQuote(value="v1", quote="q1", match_type="none"),
        SourceQuote(value="v2", quote="q2", match_type="exact", verification_score=100.0),
        SourceQuote(value="v3", quote="q3", match_type="fuzzy", verification_score=80.0, semantic_score=0.9),
        SourceQuote(value="v4", quote="q4", match_type="fuzzy", verification_score=75.0, semantic_score=0.7),
    ]
    ranked = _rank_evidence(evidence)
    assert ranked[0].match_type == "exact"
    assert ranked[1].match_type == "fuzzy"
    assert ranked[1].semantic_score == 0.9   # higher semantic score sorts first within fuzzy
    assert ranked[2].match_type == "fuzzy"
    assert ranked[3].match_type == "none"
    print("OK test_rank_evidence_orders_exact_over_fuzzy_over_none passed")


def test_aggregate_cells_source_urls_is_sorted_list():
    cells = [
        ExtractedCell(
            entity="Oatly",
            source_url="http://b.com",
            column="Q",
            value="v1",
            evidence=[SourceQuote(value="v1", quote="q1")],
        ),
        ExtractedCell(
            entity="Oatly",
            source_url="http://a.com",
            column="Q",
            value="v2",
            evidence=[SourceQuote(value="v2", quote="q2")],
        ),
    ]
    aggregated = aggregate_cells(cells)
    assert aggregated[0].source_urls == ["http://a.com", "http://b.com"]
    assert isinstance(aggregated[0].source_urls, list)
    print("OK test_aggregate_cells_source_urls_is_sorted_list passed")


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


def test_is_list_column_predicate():
    assert _is_list_column("return as a list, one claim per item") is True    # "list"
    assert _is_list_column("comma-separated values only") is True              # "comma-separated"
    assert _is_list_column("deduplicated entries") is True                     # "deduplicated"
    assert _is_list_column("list each product type found") is True             # "list"
    assert _is_list_column("For each claim return one concise sentence") is True  # "for each"
    assert _is_list_column("return one entry per item") is True                # one…per regex
    assert _is_list_column("Name the parent company only") is False
    assert _is_list_column("include specific numbers and units where stated") is False
    assert _is_list_column(None) is False
    assert _is_list_column("") is False
    print("OK test_is_list_column_predicate passed")


def test_is_list_column_production_instructions():
    """Three exact production instruction strings — only Parent company must be single-answer."""
    assert _is_list_column("For each claim return one concise sentence") is True   # Sustainability claims
    assert _is_list_column("comma-separated, deduplicated") is True                # Plant milk types
    assert _is_list_column("Name the parent company only") is False                # Parent company
    print("OK test_is_list_column_production_instructions passed")


def test_aggregate_list_column_no_conflict():
    """A list-type column with 5 distinct values must NOT set has_conflict."""
    list_cols = {"Sustainability claims"}
    # Distinct-topic strings (max pairwise token_sort_ratio ~46) so the fuzzy
    # near-duplicate dedup (_DEDUP_RATIO=85) keeps all 5. Earlier "claim {i}"
    # values collided at 85.7 once _DEDUP_RATIO was lowered 95->85 (2026-06-29),
    # collapsing to 1 — a fixture artefact, not a product bug.
    claims = [
        "solar powered manufacturing",
        "wind energy sourcing",
        "recycled packaging materials",
        "zero landfill waste target",
        "reduced water consumption",
    ]
    cells = [
        ExtractedCell(
            entity="Oatly",
            source_url=f"http://example.com/{i}",
            column="Sustainability claims",
            value=claim,
            evidence=[SourceQuote(value=claim, quote=f"quote {i}")],
        )
        for i, claim in enumerate(claims)
    ]
    aggregated = aggregate_cells(cells, list_columns=list_cols)
    assert len(aggregated) == 1
    assert aggregated[0].num_unique_values == 5
    assert aggregated[0].has_conflict is False
    print("OK test_aggregate_list_column_no_conflict passed")


def test_aggregate_single_answer_column_conflict():
    """Two genuinely different real parents MUST set has_conflict."""
    cells = [
        ExtractedCell(
            entity="Oatly",
            source_url="http://a.com",
            column="Parent company",
            value="Danone",
            evidence=[SourceQuote(value="Danone", quote="owned by Danone", verified=True)],
        ),
        ExtractedCell(
            entity="Oatly",
            source_url="http://b.com",
            column="Parent company",
            value="Nestlé",
            evidence=[SourceQuote(value="Nestlé", quote="subsidiary of Nestlé")],
        ),
    ]
    aggregated = aggregate_cells(cells)
    assert len(aggregated) == 1
    assert aggregated[0].num_unique_values == 2
    assert aggregated[0].has_conflict is True
    print("OK test_aggregate_single_answer_column_conflict passed")


def test_sentinel_suppressed_when_real_value_exists():
    """Real value + sentinel → show only real value, no conflict."""
    cells = [
        ExtractedCell(
            entity="Silk",
            source_url="http://a.com",
            column="Parent company",
            value="Danone",
            evidence=[SourceQuote(value="Danone", quote="owned by Danone", verified=True)],
        ),
        ExtractedCell(
            entity="Silk",
            source_url="http://b.com",
            column="Parent company",
            value="None (not disclosed on site)",
            evidence=[SourceQuote(value="None (not disclosed on site)", quote="no parent found")],
        ),
    ]
    aggregated = aggregate_cells(cells)
    assert len(aggregated) == 1
    agg = aggregated[0]
    assert agg.has_conflict is False
    assert agg.num_unique_values == 1          # only "Danone" counts
    assert agg.value == ["Danone"]             # sentinel excluded from display
    assert any(ev.value == "Danone" for ev in agg.evidence)
    print("OK test_sentinel_suppressed_when_real_value_exists passed")


def test_genuine_conflict_two_real_parents():
    """Two distinct real parents → conflict flagged, sentinel irrelevant."""
    cells = [
        ExtractedCell(
            entity="Silk",
            source_url="http://a.com",
            column="Parent company",
            value="Danone",
            evidence=[SourceQuote(value="Danone", quote="owned by Danone", verified=True)],
        ),
        ExtractedCell(
            entity="Silk",
            source_url="http://b.com",
            column="Parent company",
            value="Nestlé",
            evidence=[SourceQuote(value="Nestlé", quote="Nestlé subsidiary")],
        ),
    ]
    aggregated = aggregate_cells(cells)
    assert aggregated[0].has_conflict is True
    assert aggregated[0].num_unique_values == 2
    print("OK test_genuine_conflict_two_real_parents passed")


def test_sentinel_only_shows_sentinel_no_conflict():
    """All pages return sentinel → display sentinel, no conflict."""
    cells = [
        ExtractedCell(
            entity="Califia",
            source_url="http://a.com",
            column="Parent company",
            value="None (not disclosed on site)",
            evidence=[SourceQuote(value="None (not disclosed on site)", quote="no parent")],
        ),
        ExtractedCell(
            entity="Califia",
            source_url="http://b.com",
            column="Parent company",
            value="None (not disclosed on site)",
            evidence=[SourceQuote(value="None (not disclosed on site)", quote="not found")],
        ),
    ]
    aggregated = aggregate_cells(cells)
    assert len(aggregated) == 1
    agg = aggregated[0]
    assert agg.has_conflict is False
    assert agg.num_unique_values == 0          # no real values
    assert agg.value == ["None (not disclosed on site)"]
    print("OK test_sentinel_only_shows_sentinel_no_conflict passed")


def test_thin_content_gate_below_and_above_threshold():
    from src.acquire.fetcher import _thin_content_gate
    passed, reason = _thin_content_gate("x" * 199)
    assert passed is False
    assert "thin_content_199_chars" in reason

    passed, reason = _thin_content_gate("x" * 200)
    assert passed is True
    assert reason == ""
    print("OK test_thin_content_gate_below_and_above_threshold passed")


def test_firecrawl_good_content_no_fallback(monkeypatch):
    from src.acquire import fetcher as f
    monkeypatch.setattr(f, "_fetch_firecrawl_doc", lambda url, cfg: ("x" * 300, None))
    playwright_called = []
    monkeypatch.setattr(f, "_fetch_playwright", lambda url, cfg: playwright_called.append(1) or "")
    text, _, prov = f._fetch_firecrawl_with_fallback("http://x.com", Config())
    assert prov["gate_passed"] is True
    assert prov["render_fallback"] is False
    assert not playwright_called
    print("OK test_firecrawl_good_content_no_fallback passed")


def test_firecrawl_thin_triggers_playwright_fallback(monkeypatch):
    from src.acquire import fetcher as f
    monkeypatch.setattr(f, "THIN_CONTENT_FALLBACK", True)
    monkeypatch.setattr(f, "_fetch_firecrawl_doc", lambda url, cfg: ("short", None))
    monkeypatch.setattr(f, "_fetch_playwright", lambda url, cfg: "x" * 400)
    text, _, prov = f._fetch_firecrawl_with_fallback("http://x.com", Config())
    assert text == "x" * 400
    assert prov["render_fallback"] is True
    assert prov["gate_passed"] is True
    assert "thin_content_" in prov["gate_reason"]
    assert "playwright_fallback" in prov["gate_reason"]
    print("OK test_firecrawl_thin_triggers_playwright_fallback passed")


def test_firecrawl_both_thin_keeps_longer(monkeypatch):
    from src.acquire import fetcher as f
    monkeypatch.setattr(f, "THIN_CONTENT_FALLBACK", True)
    monkeypatch.setattr(f, "_fetch_firecrawl_doc", lambda url, cfg: ("fc" * 50, None))   # 100 chars
    monkeypatch.setattr(f, "_fetch_playwright", lambda url, cfg: "pw" * 30)  # 60 chars
    text, _, prov = f._fetch_firecrawl_with_fallback("http://x.com", Config())
    assert text == "fc" * 50        # firecrawl result is longer → kept
    assert prov["render_fallback"] is True
    assert prov["gate_passed"] is False
    assert "playwright_also_thin_60_chars" in prov["gate_reason"]
    print("OK test_firecrawl_both_thin_keeps_longer passed")


def test_firecrawl_thin_fallback_disabled(monkeypatch):
    from src.acquire import fetcher as f
    monkeypatch.setattr(f, "THIN_CONTENT_FALLBACK", False)
    monkeypatch.setattr(f, "_fetch_firecrawl_doc", lambda url, cfg: ("short", None))
    playwright_called = []
    monkeypatch.setattr(f, "_fetch_playwright", lambda url, cfg: playwright_called.append(1) or "")
    text, _, prov = f._fetch_firecrawl_with_fallback("http://x.com", Config())
    assert prov["gate_passed"] is False
    assert prov["render_fallback"] is False
    assert not playwright_called
    print("OK test_firecrawl_thin_fallback_disabled passed")


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
    test_rank_evidence_orders_exact_over_fuzzy_over_none()
    test_aggregate_cells_source_urls_is_sorted_list()
    test_excel_output_uses_entity_rows_and_provenance_entity()
    test_matrix_reads_cells_not_all_cells()
    test_provenance_reads_all_cells_not_cells()
    test_matrix_conflict_label()
    test_sample_input_populates_entities_urls_questions_and_config()
    test_blank_entities_url_gets_all_entities_and_specific_url_stays_scoped()
    test_extract_cells_only_returns_requested_entities()
    test_extract_cells_supports_claude_backend()
    test_backward_compatibility_without_entities_sheet()
    test_main_only_two_terminal_prompts()
    test_crawl_terms_include_questions_and_instructions_only()
    test_link_scorer_has_no_domain_specific_keyword_boosts()
    test_is_list_column_predicate()
    test_is_list_column_production_instructions()
    test_aggregate_list_column_no_conflict()
    test_aggregate_single_answer_column_conflict()
    test_sentinel_suppressed_when_real_value_exists()
    test_genuine_conflict_two_real_parents()
    test_sentinel_only_shows_sentinel_no_conflict()
    test_thin_content_gate_below_and_above_threshold()
    test_firecrawl_good_content_no_fallback(None)
    test_firecrawl_thin_triggers_playwright_fallback(None)
    test_firecrawl_both_thin_keeps_longer(None)
    test_firecrawl_thin_fallback_disabled(None)

    print("\nAll smoke tests passed!")
