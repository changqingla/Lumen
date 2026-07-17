"""Tests for dynamic model resolution security constraints."""

from __future__ import annotations

import pytest

from src.models import resolver as resolver_module


def test_dynamic_resolution_requires_thread_id(monkeypatch):
    monkeypatch.setenv(
        "MODEL_RESOLVER_INTERNAL_TOKEN",
        "model-resolver-test-token-0123456789abcdef",
    )

    with pytest.raises(ValueError, match="thread_id is required"):
        resolver_module.resolve_chat_model_spec(
            dynamic_model_token="dynamic-token",
            thread_id=None,
        )


def test_dynamic_resolution_sends_thread_id_on_every_request(monkeypatch):
    internal_token = "model-resolver-test-token-0123456789abcdef"
    monkeypatch.setenv("MODEL_RESOLVER_INTERNAL_TOKEN", internal_token)

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

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {
                "timeout": 30.0,
                "trust_env": False,
                "follow_redirects": False,
            }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, json, headers):
            calls.append(
                {
                    "url": url,
                    "json": dict(json),
                    "headers": dict(headers),
                }
            )
            return FakeResponse()

    monkeypatch.setattr(resolver_module.httpx, "Client", FakeClient)

    first = resolver_module.resolve_chat_model_spec(
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
    assert calls[0]["headers"] == {"X-Internal-Token": internal_token}
    assert first.enforce_outbound_endpoint_policy is True


@pytest.mark.parametrize(
    "token",
    [
        "",
        "short",
        "replace-with-a-random-model-resolver-token",
        "non-ascii-model-resolver-token-012345-密钥",
    ],
)
def test_dynamic_resolution_rejects_weak_internal_token(monkeypatch, token):
    monkeypatch.setenv("MODEL_RESOLVER_INTERNAL_TOKEN", token)

    with pytest.raises(ValueError, match="MODEL_RESOLVER_INTERNAL_TOKEN"):
        resolver_module.resolve_chat_model_spec(
            dynamic_model_token="dynamic-token",
            thread_id="thread-a",
        )
