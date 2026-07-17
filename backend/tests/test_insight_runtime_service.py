import os

os.environ["DEBUG"] = "false"

import pytest

from modules.chat.services.insight_runtime_service import InsightRuntimeService
from modules.chat.services import insight_runtime_service as insight_runtime_service_module


_MANAGED_FILENAME = (
    "kb__11111111-1111-1111-1111-111111111111__"
    "22222222-2222-2222-2222-222222222222__"
    "0123456789abcdef__notes.md"
)
_TEST_GATEWAY_TOKEN = "gateway-internal-test-token-0123456789"
_GATEWAY_TOKEN_HEADER = "X-Gateway-Internal-Token"


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
    assert payload["context"]["disable_model_streaming"] is False
    assert payload["config"]["recursion_limit"] == service.recursion_limit


def test_build_run_request_template_can_override_recursion_limit():
    service = InsightRuntimeService()

    payload = service.build_run_request_template(
        thread_id="thread-123",
        assistant_id="assistant-123",
        model_name="gpt-5.4",
        thinking_enabled=False,
        is_plan_mode=False,
        recursion_limit=480,
    )

    assert payload["config"]["recursion_limit"] == 480


def test_build_run_request_template_can_disable_model_streaming():
    service = InsightRuntimeService()

    payload = service.build_run_request_template(
        thread_id="thread-123",
        assistant_id="assistant-123",
        model_name="gpt-5.4",
        thinking_enabled=False,
        is_plan_mode=False,
        disable_model_streaming=True,
    )

    assert payload["context"]["disable_model_streaming"] is True


@pytest.mark.asyncio
async def test_download_thread_artifact_text_fetches_gateway_artifact(monkeypatch):
    service = InsightRuntimeService()
    service.gateway_url = "http://gateway"
    service._gateway_internal_api_token = _TEST_GATEWAY_TOKEN

    calls = []
    client_kwargs = []

    class _Response:
        content = "# 标题".encode("utf-8")

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url):
            calls.append(url)
            return _Response()

    monkeypatch.setattr(insight_runtime_service_module.httpx, "AsyncClient", _Client)

    result = await service.download_thread_artifact_text(
        "thread-123",
        "/mnt/user-data/outputs/demo.zh.md",
    )

    assert result == "# 标题"
    assert calls == [
        "http://gateway/api/threads/thread-123/artifacts/mnt/user-data/outputs/demo.zh.md?download=true"
    ]
    assert client_kwargs == [
        {
            "timeout": service.request_timeout_seconds,
            "headers": {_GATEWAY_TOKEN_HEADER: _TEST_GATEWAY_TOKEN},
            "follow_redirects": False,
            "trust_env": False,
        }
    ]


@pytest.mark.asyncio
async def test_get_thread_upload_integrity_fetches_internal_gateway_metadata(monkeypatch):
    service = InsightRuntimeService()
    service.gateway_url = "http://gateway"
    service._gateway_internal_api_token = _TEST_GATEWAY_TOKEN
    calls = []
    client_kwargs = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "filename": _MANAGED_FILENAME,
                "size": 7,
                "sha256": "a" * 64,
                "ignored": "value",
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params):
            calls.append((url, params))
            return _Response()

    monkeypatch.setattr(insight_runtime_service_module.httpx, "AsyncClient", _Client)

    result = await service.get_thread_upload_integrity("thread-123", _MANAGED_FILENAME)

    assert result == {
        "filename": _MANAGED_FILENAME,
        "size": 7,
        "sha256": "a" * 64,
    }
    assert calls == [
        (
            "http://gateway/api/threads/thread-123/uploads/metadata",
            {"filename": _MANAGED_FILENAME},
        )
    ]
    assert client_kwargs == [
        {
            "timeout": service.request_timeout_seconds,
            "headers": {_GATEWAY_TOKEN_HEADER: _TEST_GATEWAY_TOKEN},
            "follow_redirects": False,
            "trust_env": False,
        }
    ]


def test_gateway_headers_fail_closed_without_configured_token():
    service = InsightRuntimeService()
    service._gateway_internal_api_token = ""

    with pytest.raises(RuntimeError, match="GATEWAY_INTERNAL_API_TOKEN"):
        service.gateway_request_headers()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    ["../" + _MANAGED_FILENAME, "kb__not-managed.md", "notes.md"],
)
async def test_get_thread_upload_integrity_rejects_invalid_managed_filename(filename):
    service = InsightRuntimeService()

    with pytest.raises(ValueError, match="受管知识文件名"):
        await service.get_thread_upload_integrity("thread-123", filename)


@pytest.mark.asyncio
async def test_has_active_thread_run_checks_running_and_pending(monkeypatch):
    service = InsightRuntimeService()
    service.langgraph_url = "http://langgraph"
    calls = []
    client_kwargs = []

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            client_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params):
            calls.append((url, params))
            payload = [] if params["status"] == "running" else [{"run_id": "run-1"}]
            return _Response(payload)

    monkeypatch.setattr(insight_runtime_service_module.httpx, "AsyncClient", _Client)

    assert await service.has_active_thread_run("thread-123") is True
    assert calls == [
        (
            "http://langgraph/threads/thread-123/runs",
            {"limit": 1, "status": "running"},
        ),
        (
            "http://langgraph/threads/thread-123/runs",
            {"limit": 1, "status": "pending"},
        ),
    ]
    assert client_kwargs == [
        {
            "timeout": service.request_timeout_seconds,
            "follow_redirects": False,
            "trust_env": False,
        }
    ]


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
