"""Tests for dynamic model resolution security constraints."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.models import resolver as resolver_module


def test_dynamic_resolution_requires_thread_id(monkeypatch):
    monkeypatch.setenv("RAG_INTERNAL_API_TOKEN", "internal-token")

    with pytest.raises(ValueError, match="thread_id is required"):
        resolver_module.resolve_chat_model_spec(
            dynamic_model_token="dynamic-token",
            thread_id=None,
        )

def test_dynamic_resolution_sends_thread_id_on_every_request(monkeypatch):
    monkeypatch.setenv("RAG_INTERNAL_API_TOKEN", "internal-token")

    calls: list[dict] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "name": "user-model:demo",
                "display_name": "Demo",
                "description": None,
                "use": "langchain_openai:ChatOpenAI",
                "config": {"model": "gpt-4.1-mini", "api_key": "secret"},
                "supports_vision": False,
                "supports_thinking": False,
                "supports_reasoning_effort": False,
            }

    def _fake_post(url, json, headers, timeout):
        calls.append(
            {
                "url": url,
                "json": dict(json),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr(resolver_module.httpx, "post", _fake_post)

    resolver_module.resolve_chat_model_spec(
        dynamic_model_token="dynamic-token",
        thread_id="thread-a",
    )
    resolver_module.resolve_chat_model_spec(
        dynamic_model_token="dynamic-token",
        thread_id="thread-a",
    )
    resolver_module.resolve_chat_model_spec(
        dynamic_model_token="dynamic-token",
        thread_id="thread-b",
    )

    assert len(calls) == 3
    assert calls[0]["json"] == {"token": "dynamic-token", "thread_id": "thread-a"}
    assert calls[1]["json"] == {"token": "dynamic-token", "thread_id": "thread-a"}
    assert calls[2]["json"] == {"token": "dynamic-token", "thread_id": "thread-b"}
