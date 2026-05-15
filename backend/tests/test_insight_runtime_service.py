import os

os.environ.setdefault("DEBUG", "false")

import pytest

from modules.chat.services.insight_runtime_service import InsightRuntimeService
from modules.chat.services import insight_runtime_service as insight_runtime_service_module


def test_build_run_request_template_uses_messages_tuple_stream_mode():
    service = InsightRuntimeService()

    payload = service.build_run_request_template(
        thread_id="thread-123",
        assistant_id="assistant-123",
        model_name="gpt-5.4",
        thinking_enabled=True,
        is_plan_mode=False,
    )

    assert payload["stream_mode"] == ["messages-tuple", "values", "custom"]


@pytest.mark.asyncio
async def test_resolve_assistant_id_creates_when_search_has_no_exact_match(monkeypatch):
    service = InsightRuntimeService()
    service.langgraph_url = "http://langgraph"
    service.assistant_id = "lumen-agent"

    calls: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            calls.append((url, json))
            if url.endswith("/assistants/search"):
                return _Response(
                    [
                        {
                            "assistant_id": "unrelated-assistant",
                            "name": "other-agent",
                            "metadata": {"created_by": "user"},
                        }
                    ]
                )
            return _Response({"assistant_id": "created-assistant"})

    monkeypatch.setattr(insight_runtime_service_module.httpx, "AsyncClient", _Client)

    resolved = await service.resolve_assistant_id()

    assert resolved == "created-assistant"
    assert calls == [
        ("http://langgraph/assistants/search", {"graph_id": "lumen-agent", "limit": 10}),
        (
            "http://langgraph/assistants",
            {"graph_id": "lumen-agent", "config": {}, "metadata": {"source": "lumen"}},
        ),
    ]
