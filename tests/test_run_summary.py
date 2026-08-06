"""The end-of-run summary must be computable from any diag dict and honest
about zero-yield sites (production handoff requirement)."""
from main import print_run_summary
from models import ColumnSpec, ExtractedCell, ExtractedRow, PipelineInput, PipelineResult, UrlSpec


def test_run_summary_reports_dead_seeds_and_cache_split(capsys):
    result = PipelineResult(rows=[
        ExtractedRow(entity="Acme", cells=[
            ExtractedCell(entity="Acme", source_url="https://acme.com", column="HQ?", value="Berlin"),
            ExtractedCell(entity="Acme", source_url="https://acme.com", column="Rev?", value=None),
        ]),
        ExtractedRow(entity="Blocked Co", cells=[]),
    ])
    pi = PipelineInput(
        entities=["Acme", "Blocked Co"],
        urls=[UrlSpec(url="https://acme.com", depth=1, entities=["Acme"])],
        columns=[ColumnSpec(name="HQ?"), ColumnSpec(name="Rev?")],
    )
    diag = {
        "acquire_log": [
            {"entity_url": "https://acme.com", "status": "ok"},
            {"entity_url": "https://acme.com", "status": "ok"},
            {"entity_url": "https://blocked.co", "status": "gate_failed"},
        ],
        "extract_log": [
            {"extraction_time_ms": 900},
            {"extraction_time_ms": 0},   # cache hit
        ],
    }
    print_run_summary(result, pi, diag, elapsed=61)
    out = capsys.readouterr().out
    assert "2 usable" in out and "1 failed" in out
    assert "LLM calls:       1" in out and "+1 served from cache" in out
    assert "1/4" in out                       # populated cells
    assert "https://blocked.co" in out        # dead seed named, not hidden
    assert "1m 1s" in out


def test_run_summary_handles_empty_diag(capsys):
    result = PipelineResult(rows=[])
    pi = PipelineInput(entities=[], urls=[], columns=[])
    print_run_summary(result, pi, {}, elapsed=5)
    assert "RUN SUMMARY" in capsys.readouterr().out
