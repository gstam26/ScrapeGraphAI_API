from models import ExtractedCell, PageDoc, SourceQuote
from src.verify import verify_cell, verify_cells


def _page() -> PageDoc:
    return PageDoc(
        url="https://example.test",
        text="Oatly reports lower climate impact from oat-based drinks.",
    )


def test_exact_match_sets_span_and_match_type():
    quote = "lower climate impact"
    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value="lower climate impact",
        evidence=[SourceQuote(value="lower climate impact", quote=quote)],
    )

    verified = verify_cell(cell, _page())

    start = _page().text.find(quote)
    assert verified.verified is True
    assert verified.evidence[0].verified is True
    assert verified.evidence[0].match_type == "exact"
    assert verified.evidence[0].char_span == (start, start + len(quote))


def test_fuzzy_match_sets_match_type_without_span():
    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value="lower climate impacts",
        evidence=[SourceQuote(value="lower climate impacts", quote="lower climate impacts")],
    )

    verified = verify_cell(cell, _page())

    assert verified.verified is True
    assert verified.evidence[0].verified is True
    assert verified.evidence[0].match_type == "fuzzy"
    assert verified.evidence[0].char_span is None


def test_no_quote_cell_unverified():
    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value="lower climate impact",
        evidence=[SourceQuote(value="lower climate impact", quote=None)],
    )

    verified = verify_cell(cell, _page())

    assert verified.verified is False
    assert verified.evidence[0].verified is False
    assert verified.evidence[0].match_type == "none"
    assert verified.evidence[0].char_span is None


def test_empty_evidence_cell_unverified():
    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value="lower climate impact",
        evidence=[],
    )

    verified = verify_cell(cell, _page())

    assert verified.verified is False
    assert verified.verification_score is None


def test_mixed_verified_unverified_evidence_cell_unverified():
    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value=["lower climate impact", "unsupported"],
        evidence=[
            SourceQuote(value="lower climate impact", quote="lower climate impact"),
            SourceQuote(value="unsupported", quote="not present on this page"),
        ],
    )

    verified = verify_cell(cell, _page())

    assert verified.verified is False
    assert verified.evidence[0].verified is True
    assert verified.evidence[0].match_type == "exact"
    assert verified.evidence[1].verified is False
    assert verified.evidence[1].match_type == "none"


def test_semantic_score_populated_when_claim_and_quote_exist(monkeypatch):
    """semantic_score is set when both claim value and quote are present."""
    def mock_embed(texts):
        return [[1.0, 0.0, 0.0]] * len(texts)

    monkeypatch.setattr("src.embed.embed_batch", mock_embed)

    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value="lower climate impact",
        evidence=[SourceQuote(value="lower climate impact", quote="lower climate impact")],
    )

    result = verify_cells([cell], _page())

    assert result[0].evidence[0].semantic_score is not None
    assert abs(result[0].evidence[0].semantic_score - 1.0) < 1e-4


def test_semantic_score_none_when_no_quote():
    """semantic_score stays None when the evidence item has no quote."""
    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value="lower climate impact",
        evidence=[SourceQuote(value="lower climate impact", quote=None)],
    )

    result = verify_cells([cell], _page())

    assert result[0].evidence[0].semantic_score is None


def test_verified_decision_unchanged_by_semantic_score(monkeypatch):
    """Low semantic similarity does not flip a rapidfuzz-verified item to unverified."""
    def mock_embed(texts):
        # Alternating orthogonal vectors → cosine = 0.0 for each (claim, quote) pair
        return [[1.0, 0.0] if i % 2 == 0 else [0.0, 1.0] for i in range(len(texts))]

    monkeypatch.setattr("src.embed.embed_batch", mock_embed)

    quote = "lower climate impact"
    cell = ExtractedCell(
        entity="Oatly",
        source_url="https://example.test",
        column="Claim",
        value="lower climate impact",
        evidence=[SourceQuote(value="lower climate impact", quote=quote)],
    )

    result = verify_cells([cell], _page())

    ev = result[0].evidence[0]
    assert ev.verified is True           # rapidfuzz exact match still holds
    assert ev.semantic_score is not None
    assert ev.semantic_score < 0.5       # semantic is low (orthogonal vectors)
    assert result[0].verified is True    # cell-level decision unchanged
