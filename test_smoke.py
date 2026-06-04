"""
Smoke tests for entity extraction pipeline.

Tests:
- Parser handles dict output
- Parser handles list-of-dicts output
- Parser handles scalar output
- Aggregator does not drop evidence-only cells
- Excel provenance produces rows for list evidence
"""

from models import PageDoc, ColumnSpec, ExtractedCell, SourceQuote
from extract import _parse_field_value
from aggregate import aggregate_cells


def test_parse_dict_output():
    """Test parsing dict response (value + quote)."""
    raw = {
        "value": "test value",
        "quote": "test quote from page"
    }
    
    value, evidence = _parse_field_value(raw)
    
    assert value == "test value"
    assert len(evidence) == 1
    assert evidence[0].value == "test value"
    assert evidence[0].quote == "test quote from page"
    print("✓ test_parse_dict_output passed")


def test_parse_list_of_dicts():
    """Test parsing list of dict responses."""
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
    print("✓ test_parse_list_of_dicts passed")


def test_parse_scalar_output():
    """Test parsing scalar (plain value, no quote)."""
    raw = "plain text value"
    
    value, evidence = _parse_field_value(raw)
    
    assert value == "plain text value"
    assert len(evidence) == 1
    assert evidence[0].value == "plain text value"
    assert evidence[0].quote is None
    print("✓ test_parse_scalar_output passed")


def test_parse_null_output():
    """Test parsing null output."""
    raw = None
    
    value, evidence = _parse_field_value(raw)
    
    assert value is None
    assert len(evidence) == 0
    print("✓ test_parse_null_output passed")


def test_aggregator_preserves_evidence_only():
    """Test that aggregator does not drop evidence-only cells."""
    cells = [
        ExtractedCell(
            source_url="http://example.com/1",
            column="test_col",
            value=None,  # No value, only evidence
            evidence=[
                SourceQuote(value="found via evidence", quote="supporting quote")
            ],
            verified=True,
            verification_score=90.0,
        ),
        ExtractedCell(
            source_url="http://example.com/2",
            column="test_col",
            value="direct value",
            evidence=[
                SourceQuote(value="direct value", quote="direct quote")
            ],
            verified=False,
            verification_score=50.0,
        ),
    ]
    
    aggregated = aggregate_cells(cells)
    
    # Should keep the direct value over evidence-only
    assert len(aggregated) == 1
    assert aggregated[0].value == "direct value"
    assert aggregated[0].source_url == "http://example.com/2"
    print("✓ test_aggregator_preserves_evidence_only passed")


def test_aggregator_prefers_verified():
    """Test that aggregator prefers verified cells."""
    cells = [
        ExtractedCell(
            source_url="http://example.com/1",
            column="test_col",
            value="unverified value",
            evidence=[SourceQuote(value="unverified", quote="quote")],
            verified=False,
            verification_score=40.0,
        ),
        ExtractedCell(
            source_url="http://example.com/2",
            column="test_col",
            value="verified value",
            evidence=[SourceQuote(value="verified", quote="quote")],
            verified=True,
            verification_score=85.0,
        ),
    ]
    
    aggregated = aggregate_cells(cells)
    
    # Should prefer verified
    assert len(aggregated) == 1
    assert aggregated[0].verified is True
    assert aggregated[0].source_url == "http://example.com/2"
    print("✓ test_aggregator_prefers_verified passed")


def test_excel_provenance_multiple_evidence():
    """Test that Excel provenance handles multiple evidence items per cell."""
    from io_excel import write_output_excel
    from models import PipelineResult, ExtractedRow
    import os
    import tempfile
    import pandas as pd
    
    columns = [ColumnSpec(name="test_col")]
    
    result = PipelineResult(
        rows=[
            ExtractedRow(
                entity_url="http://example.com",
                cells=[
                    ExtractedCell(
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
        
        # Read and check provenance sheet
        provenance_df = pd.read_excel(output_path, sheet_name="Provenance")
        
        # Should have 2 rows: one per evidence item
        assert len(provenance_df) == 2
        assert provenance_df.iloc[0]["Value"] == "item 1"
        assert provenance_df.iloc[1]["Value"] == "item 2"
        assert provenance_df.iloc[0]["Quote"] == "quote 1"
        assert provenance_df.iloc[1]["Quote"] == "quote 2"
        
        print("✓ test_excel_provenance_multiple_evidence passed")


if __name__ == "__main__":
    test_parse_dict_output()
    test_parse_list_of_dicts()
    test_parse_scalar_output()
    test_parse_null_output()
    test_aggregator_preserves_evidence_only()
    test_aggregator_prefers_verified()
    test_excel_provenance_multiple_evidence()
    
    print("\n✅ All smoke tests passed!")
