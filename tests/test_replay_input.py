"""
Tests for diagnostics/build_replay_input.py — the page-set-pinning replay
tool (standing requirement, decision-log 2026-07-06: before/after validations
must replay a pinned URL list, never re-crawl).

Fully offline: builds a fake baseline output workbook (Acquire Log) and a
fake original input workbook with pandas, runs the builder, and validates the
result through the REAL src.io_excel.read_input — the same reader main.py
uses — so the round-trip is proven against production parsing, not a mock.
"""
import pandas as pd
import pytest

from diagnostics.build_replay_input import build_replay_input
from src.io_excel import read_input

_Q = ["R&D location", "Recent news"]
_INSTR = ["Where is R&D conducted? Check about pages.",
          "List recent announcements, one per item."]


def _write_input(path, entities=("Alpha Co", "Beta Co", "Gamma Co")) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({"entity": list(entities)}).to_excel(w, sheet_name="entities", index=False)
        pd.DataFrame({
            "url": ["https://alpha.example.com", "https://beta.example.com"],
            "depth": [1, 1],
            "entities": ["Alpha Co", "Beta Co"],
        }).to_excel(w, sheet_name="urls", index=False)
        pd.DataFrame({"question": _Q, "instructions": _INSTR}).to_excel(
            w, sheet_name="questions", index=False)
        pd.DataFrame({"setting": ["EXTRACT_TOOL"], "value": ["llmapi"]}).to_excel(
            w, sheet_name="config", index=False)


def _write_baseline(path, rows) -> None:
    cols = ["Entities", "Seed URL", "Page URL", "Depth", "Status"]
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({"Entity": ["Alpha Co"]}).to_excel(w, sheet_name="Summary", index=False)
        pd.DataFrame(rows, columns=cols).to_excel(w, sheet_name="Acquire Log", index=False)


_BASELINE_ROWS = [
    # Alpha: 2 contentful pages (one crawl-path "ok", one direct-path "cached"),
    # 1 duplicate of the first, 1 error, 1 gate_failed.
    ("Alpha Co", "https://alpha.example.com", "https://alpha.example.com", 0, "ok"),
    ("Alpha Co", "https://alpha.example.com", "https://alpha.example.com/about", 1, "cached"),
    ("Alpha Co", "https://alpha.example.com", "https://alpha.example.com", 0, "ok"),
    ("Alpha Co", "https://alpha.example.com", "https://alpha.example.com/broken", 1, "error"),
    ("Alpha Co", "https://alpha.example.com", "https://alpha.example.com/thin", 1, "gate_failed"),
    # Beta: 1 contentful page. Gamma: none (entity with zero pages).
    ("Beta Co", "https://beta.example.com", "https://beta.example.com/news", 1, "ok"),
]


def test_replay_pins_contentful_pages_at_depth_zero(tmp_path):
    inp = str(tmp_path / "input.xlsx")
    base = str(tmp_path / "baseline.xlsx")
    out = str(tmp_path / "replay.xlsx")
    _write_input(inp)
    _write_baseline(base, _BASELINE_ROWS)

    summary = build_replay_input(base, inp, out)

    assert summary["pages"] == 3  # 2 Alpha (deduped) + 1 Beta
    assert summary["entities_with_pages"] == 2
    assert summary["entities_without_pages"] == ["Gamma Co"]
    assert summary["skipped_by_status"] == {"error": 1, "gate_failed": 1}

    # Round-trip through the production reader.
    replay = read_input(out)
    assert set(replay.entities) == {"Alpha Co", "Beta Co", "Gamma Co"}
    assert [(s.url, s.depth, s.entities) for s in replay.urls] == [
        ("https://alpha.example.com", 0, ["Alpha Co"]),
        ("https://alpha.example.com/about", 0, ["Alpha Co"]),
        ("https://beta.example.com/news", 0, ["Beta Co"]),
    ], "exact baseline page set, all depth 0 (direct-fetch path, no crawl)"
    # Questions keep their instructions (the output workbook never stores
    # these — they must come from the original input).
    assert [(c.name, c.instruction) for c in replay.columns] == list(zip(_Q, _INSTR))
    assert replay.config_overrides == {"EXTRACT_TOOL": "llmapi"}
    print("OK test_replay_pins_contentful_pages_at_depth_zero passed")


def test_replay_rejects_mismatched_input_workbook(tmp_path):
    """Baseline entities absent from the input's entities sheet = wrong
    --input (or comma-carrying names): fail loudly, before read_input would
    fail obscurely."""
    inp = str(tmp_path / "input.xlsx")
    base = str(tmp_path / "baseline.xlsx")
    _write_input(inp, entities=("Alpha Co",))  # Beta Co missing
    _write_baseline(base, _BASELINE_ROWS)

    with pytest.raises(ValueError, match="Beta Co"):
        build_replay_input(base, inp, str(tmp_path / "replay.xlsx"))
    print("OK test_replay_rejects_mismatched_input_workbook passed")


def test_replay_requires_acquire_log(tmp_path):
    inp = str(tmp_path / "input.xlsx")
    base = str(tmp_path / "no_log.xlsx")
    _write_input(inp)
    with pd.ExcelWriter(base, engine="openpyxl") as w:
        pd.DataFrame({"Entity": ["Alpha Co"]}).to_excel(w, sheet_name="Summary", index=False)

    with pytest.raises(ValueError, match="Acquire Log"):
        build_replay_input(base, inp, str(tmp_path / "replay.xlsx"))
    print("OK test_replay_requires_acquire_log passed")


def test_replay_requires_some_contentful_pages(tmp_path):
    inp = str(tmp_path / "input.xlsx")
    base = str(tmp_path / "all_failed.xlsx")
    _write_input(inp)
    _write_baseline(base, [
        ("Alpha Co", "https://alpha.example.com", "https://alpha.example.com", 0, "error"),
    ])

    with pytest.raises(ValueError, match="[Nn]othing to replay"):
        build_replay_input(base, inp, str(tmp_path / "replay.xlsx"))
    print("OK test_replay_requires_some_contentful_pages passed")
