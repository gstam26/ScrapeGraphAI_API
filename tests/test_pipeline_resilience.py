"""
Guards against the entity-parallelism crash-propagation bug found in the
2026-07-03 code review: a print() call sat outside _process_url_spec's try
block, so an encoding error there (realistic on Windows consoles for
non-ASCII entity names/URLs) propagated through the unguarded future.result()
in run_pipeline and discarded every already-completed entity's results.

Two guards:
  1. _safe_print never raises on encoding, closing the actual trigger.
  2. run_pipeline's entity-level future collection has its own try/except
     backstop (mirroring the pre-existing page-level pattern), so any other
     unexpected per-spec exception still can't take down the whole run.
"""
import pipeline as pipeline_mod
from models import ColumnSpec, PipelineInput, UrlSpec


class _NarrowStream:
    """Simulates a Windows console limited to a narrow codepage — any
    non-ASCII character raises UnicodeEncodeError on write(), matching how a
    real narrow-codepage stdout fails inside print()."""
    encoding = "ascii"

    def __init__(self):
        self.written: list[str] = []

    def write(self, s: str) -> None:
        s.encode("ascii")
        self.written.append(s)

    def flush(self) -> None:
        pass


def test_safe_print_survives_encoding_error(monkeypatch):
    stream = _NarrowStream()
    monkeypatch.setattr(pipeline_mod.sys, "stdout", stream)

    pipeline_mod._safe_print("Company: Bruker — Ettlingen, Germany")  # em dash, not ASCII

    output = "".join(stream.written)
    assert output, "fallback must still produce output, not swallow it"
    assert "Bruker" in output and "Ettlingen" in output
    print("OK test_safe_print_survives_encoding_error passed")


def test_safe_print_passthrough_for_plain_text(monkeypatch):
    stream = _NarrowStream()
    monkeypatch.setattr(pipeline_mod.sys, "stdout", stream)

    pipeline_mod._safe_print("Company: Bruker")
    assert "".join(stream.written) == "Company: Bruker\n"
    print("OK test_safe_print_passthrough_for_plain_text passed")


def test_run_pipeline_survives_one_spec_crashing(monkeypatch):
    """One spec raising an exception that _process_url_spec's own try/except
    cannot attribute (simulated here directly) must not lose the other
    already-completed specs' results."""
    calls = []

    def fake_process(spec, request, cfg, all_entities):
        calls.append(spec.url)
        if spec.url == "https://bad.example.com":
            raise RuntimeError("unexpected crash outside the normal error path")
        return {
            "entities": spec.entities or all_entities,
            "diag": {"acquire_log": [], "crawl_candidates": [], "filter_log": [], "extract_log": [], "verify_log": []},
            "pages": [], "cells": [], "extract_time_ms": 0, "error": None,
        }

    monkeypatch.setattr(pipeline_mod, "_process_url_spec", fake_process)

    request = PipelineInput(
        entities=["Good Co", "Bad Co"],
        urls=[
            UrlSpec(url="https://good.example.com", depth=0, entities=["Good Co"]),
            UrlSpec(url="https://bad.example.com", depth=0, entities=["Bad Co"]),
        ],
        columns=[ColumnSpec(name="Q1")],
    )

    result, diag = pipeline_mod.run_pipeline(request)

    assert len(calls) == 2, "both specs must have been attempted"
    entities_in_result = {row.entity for row in result.rows}
    assert entities_in_result == {"Good Co", "Bad Co"}, (
        "run_pipeline must return a row for every entity even when one spec crashed"
    )
    print("OK test_run_pipeline_survives_one_spec_crashing passed")
