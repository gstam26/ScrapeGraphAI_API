"""Extract-layer unit tests (2026-07-23 review): prompt rules, entity
context, cache-key sensitivity, Azure determinism params + retry.
All LLM calls mocked — offline suite."""
import sys
import types

import src.extract as extract_mod
from models import ColumnSpec, PageDoc


def _cols():
    return [ColumnSpec(name="Where is the company headquarters located?",
                       instruction="City and country.")]


# ── prompt rules ─────────────────────────────────────────────────────────────

def test_prompt_forbids_page_level_not_disclosed():
    prompt = extract_mod._build_prompt(_cols(), ["Acme"], "some page text")
    assert '"Not disclosed", "not found", "unknown"' in prompt
    assert "use null" in prompt
    print("OK test_prompt_forbids_page_level_not_disclosed passed")


def test_prompt_includes_entity_context_only_when_given():
    with_ctx = extract_mod._build_prompt(
        _cols(), ["Minnetronix Medical"], "text",
        entity_context="Acquired by Forj Medical; this site is the acquirer's.")
    assert "Context about these entities" in with_ctx
    assert "Forj Medical" in with_ctx
    without = extract_mod._build_prompt(_cols(), ["Minnetronix Medical"], "text")
    assert "Context about these entities" not in without
    print("OK test_prompt_includes_entity_context_only_when_given passed")


# ── cache key sensitivity ────────────────────────────────────────────────────

def test_cache_key_changes_with_instruction_and_context_and_version(monkeypatch):
    base = extract_mod._extract_cache_key("chunk", _cols(), ["Acme"], "azure")

    changed_instr = [ColumnSpec(name=_cols()[0].name, instruction="DIFFERENT")]
    assert extract_mod._extract_cache_key("chunk", changed_instr, ["Acme"], "azure") != base

    assert extract_mod._extract_cache_key(
        "chunk", _cols(), ["Acme"], "azure", entity_context="ctx") != base

    monkeypatch.setattr(extract_mod, "EXTRACT_PROMPT_VERSION", "e999")
    assert extract_mod._extract_cache_key("chunk", _cols(), ["Acme"], "azure") != base

    print("OK test_cache_key_changes_with_instruction_and_context_and_version passed")


# ── Azure determinism + retry ────────────────────────────────────────────────

class _FakeCompletion:
    class _Choice:
        class _Msg:
            content = '{"Acme": {"Where is the company headquarters located?": null}}'
        message = _Msg()
    choices = [_Choice()]


def _install_fake_openai(monkeypatch, create_fn):
    fake_openai = types.ModuleType("openai")

    class FakeClient:
        def __init__(self, base_url=None, api_key=None):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create_fn))

    fake_openai.OpenAI = FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)


def test_azure_sends_temperature_zero_and_seed(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return _FakeCompletion()

    _install_fake_openai(monkeypatch, create)
    monkeypatch.setattr(extract_mod, "AZURE_API_KEY", "test-key")

    page = PageDoc(url="http://x.com", text="Acme HQ is in Lund, Sweden.")
    data, timing = extract_mod._extract_with_azure(page, _cols(), ["Acme"])
    assert captured["temperature"] == 0.0
    assert captured["seed"] == extract_mod.EXTRACT_SEED
    assert isinstance(data, dict)
    print("OK test_azure_sends_temperature_zero_and_seed passed")


def test_azure_retries_once_on_transient_error(monkeypatch):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("502 Bad Gateway")
        return _FakeCompletion()

    _install_fake_openai(monkeypatch, create)
    monkeypatch.setattr(extract_mod, "AZURE_API_KEY", "test-key")
    monkeypatch.setattr(extract_mod.time, "sleep", lambda s: None)

    page = PageDoc(url="http://x.com", text="text")
    data, timing = extract_mod._extract_with_azure(page, _cols(), ["Acme"])
    assert calls["n"] == 2, "one retry expected on transient error"
    assert timing["retry_count"] == 1
    assert isinstance(data, dict) and data, "second attempt's result must be used"
    print("OK test_azure_retries_once_on_transient_error passed")


def test_azure_timeout_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise RuntimeError("Request timed out")

    _install_fake_openai(monkeypatch, create)
    monkeypatch.setattr(extract_mod, "AZURE_API_KEY", "test-key")

    page = PageDoc(url="http://x.com", text="text")
    data, timing = extract_mod._extract_with_azure(page, _cols(), ["Acme"])
    assert calls["n"] == 1 and timing["timed_out"] is True and data == {}
    print("OK test_azure_timeout_does_not_retry passed")
