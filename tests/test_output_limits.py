"""
Guards against the HORIBA news-archive failure mode (2026-07-02 validation):
a 735 KB archive page produced 95 chunks / 654 claims and a Matrix cell that
hit Excel's 32,767-char hard limit and was silently truncated.

Three bounds, all explicit and never silent:
  1. EXTRACT_MAX_CHUNKS_PER_PAGE caps per-page LLM cost
  2. MATRIX_MAX_DISPLAY_ITEMS caps rendered bullets (with overflow marker)
  3. _clamp_cell_text enforces the Excel limit (with truncation marker)
"""
from config import EXTRACT_MAX_CHUNKS_PER_PAGE, MATRIX_MAX_DISPLAY_ITEMS
from models import ColumnSpec, ExtractedCell, ExtractedRow, PipelineResult, SourceQuote
from src.extract import _chunk_text
from src.io_excel import _EXCEL_CELL_MAX, _clamp_cell_text, _make_matrix_df


def test_chunking_capped_on_pathological_page():
    """A 735 KB-scale page must yield at most EXTRACT_MAX_CHUNKS_PER_PAGE chunks."""
    text = "x" * 800_000
    chunks = _chunk_text(text, 8000, 200)
    assert len(chunks) == EXTRACT_MAX_CHUNKS_PER_PAGE
    print("OK test_chunking_capped_on_pathological_page passed")


def test_chunking_unchanged_for_normal_pages():
    """The Oatly-report scale (~113 KB, the locked-benchmark maximum) must be
    untouched by the cap."""
    text = "y" * 113_751
    chunks = _chunk_text(text, 8000, 200)
    assert len(chunks) < EXTRACT_MAX_CHUNKS_PER_PAGE
    # reconstructable: last chunk ends where the text ends
    assert chunks[-1][-1] == "y" and sum(len(c) for c in chunks) >= len(text)
    print("OK test_chunking_unchanged_for_normal_pages passed")


def _row_with_n_items(n: int) -> PipelineResult:
    values = [f"news item number {i} announcing product line {i}" for i in range(n)]
    cell = ExtractedCell(
        entity="HORIBA",
        source_url="https://example.com/news",
        column="Recent news",
        value=values,
        evidence=[SourceQuote(value=v, quote=v, verified=True) for v in values],
        verified=True,
    )
    return PipelineResult(rows=[ExtractedRow(entity="HORIBA", cells=[cell], all_cells=[cell])])


def test_matrix_caps_display_items_with_marker():
    result = _row_with_n_items(200)
    df, _ = _make_matrix_df(result, [ColumnSpec(name="Recent news")])
    cell = df.iloc[0]["Recent news"]
    bullets = [l for l in cell.split("\n") if l.startswith("- ")]
    assert len(bullets) == MATRIX_MAX_DISPLAY_ITEMS, f"expected cap, got {len(bullets)} bullets"
    assert f"[+{200 - MATRIX_MAX_DISPLAY_ITEMS} more items — see Provenance]" in cell
    assert len(cell) < _EXCEL_CELL_MAX
    print("OK test_matrix_caps_display_items_with_marker passed")


def test_matrix_small_cells_unchanged():
    result = _row_with_n_items(3)
    df, _ = _make_matrix_df(result, [ColumnSpec(name="Recent news")])
    cell = df.iloc[0]["Recent news"]
    assert cell.count("- news item") == 3 and "more items" not in cell
    print("OK test_matrix_small_cells_unchanged passed")


def test_clamp_cell_text_marks_truncation_on_line_boundary():
    long_text = "\n".join("- " + ("z" * 100) for _ in range(500))  # ~51k chars
    clamped = _clamp_cell_text(long_text)
    assert len(clamped) <= _EXCEL_CELL_MAX
    assert clamped.endswith("[truncated — full list in Provenance]")
    body = clamped.rsplit("\n", 1)[0]
    assert body.endswith("z"), "must cut on a line boundary, not mid-claim"
    print("OK test_clamp_cell_text_marks_truncation_on_line_boundary passed")


def test_clamp_cell_text_noop_under_limit():
    assert _clamp_cell_text("short") == "short"
    print("OK test_clamp_cell_text_noop_under_limit passed")
