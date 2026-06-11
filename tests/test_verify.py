from models import ExtractedCell, PageDoc, SourceQuote
from src.verify import verify_cell


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
