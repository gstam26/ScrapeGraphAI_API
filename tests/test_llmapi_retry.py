"""
Unit tests for LLMAPI 5xx retry behaviour. No network calls.

Context: the Power Automate proxy intermittently returns 502 Bad Gateway under
load (observed once in the 25-company validation run, 2026-07-02); without a
retry the affected chunk's cells are silently blanked.
"""
import pytest
import requests

from src.llmapi import LLMAPI


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        return self._payload


def _patched_llm(monkeypatch, responses: list[_FakeResponse]) -> tuple[LLMAPI, list]:
    monkeypatch.setenv("LLM_API_URL", "https://example.invalid/flow")
    calls: list = []

    def fake_post(self, url, json=None, timeout=None):
        calls.append(url)
        return responses[len(calls) - 1]

    monkeypatch.setattr(LLMAPI, "post", fake_post)
    monkeypatch.setattr("src.llmapi.time.sleep", lambda s: None)
    return LLMAPI(), calls


def test_llmapi_retries_once_on_502(monkeypatch):
    """A single 502 followed by a 200 must succeed transparently."""
    llm, calls = _patched_llm(monkeypatch, [
        _FakeResponse(502),
        _FakeResponse(200, {"response": "ok"}),
    ])
    assert llm.call("hi") == "ok"
    assert len(calls) == 2, "expected exactly one retry"
    print("OK test_llmapi_retries_once_on_502 passed")


def test_llmapi_gives_up_after_max_attempts(monkeypatch):
    """Persistent 5xx must surface as HTTPError after max_attempts, not loop."""
    llm, calls = _patched_llm(monkeypatch, [
        _FakeResponse(502),
        _FakeResponse(502),
    ])
    with pytest.raises(requests.exceptions.HTTPError):
        llm.call("hi")
    assert len(calls) == 2
    print("OK test_llmapi_gives_up_after_max_attempts passed")


def test_llmapi_4xx_not_retried(monkeypatch):
    """Client errors are not transient — no retry."""
    llm, calls = _patched_llm(monkeypatch, [_FakeResponse(403)])
    with pytest.raises(requests.exceptions.HTTPError):
        llm.call("hi")
    assert len(calls) == 1
    print("OK test_llmapi_4xx_not_retried passed")


def test_llmapi_timeout_still_raises_timeout_error(monkeypatch):
    """Timeouts keep the existing contract (TimeoutError, caller handles)."""
    monkeypatch.setenv("LLM_API_URL", "https://example.invalid/flow")

    def fake_post(self, url, json=None, timeout=None):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(LLMAPI, "post", fake_post)
    llm = LLMAPI()
    with pytest.raises(TimeoutError):
        llm.call("hi")
    print("OK test_llmapi_timeout_still_raises_timeout_error passed")
