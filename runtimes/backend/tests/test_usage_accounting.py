from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from src.agents.middlewares import usage_accounting_middleware as middleware_module
from src.agents.middlewares import usage_summarization_middleware as summary_module
from src.agents.middlewares.usage_accounting_middleware import UsageAccountingMiddleware
from src.agents.middlewares.usage_summarization_middleware import UsageSummarizationMiddleware
from src.usage import accounting


@pytest.fixture(autouse=True)
def clear_usage_states():
    with accounting._states_lock:
        accounting._states.clear()
    yield
    with accounting._states_lock:
        accounting._states.clear()


def test_measurement_prefers_usage_metadata():
    response = ModelResponse(
        result=[
            AIMessage(
                content="answer",
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                },
            )
        ]
    )
    measurement = accounting.measure_model_response(
        response,
        request_messages=[HumanMessage(content="question")],
    )
    assert measurement == accounting.TokenMeasurement(120, 30, 150, "usage_metadata")


def test_measurement_supports_openai_response_metadata():
    response = AIMessage(
        content="answer",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 19,
            }
        },
    )
    measurement = accounting.measure_model_response(response)
    assert measurement == accounting.TokenMeasurement(12, 7, 19, "response_metadata")


def test_measurement_falls_back_to_runtime_estimate():
    measurement = accounting.measure_model_response(
        AIMessage(content="an answer without provider usage"),
        request_messages=[HumanMessage(content="question")],
    )
    assert measurement.source == "estimated"
    assert measurement.input_tokens > 0
    assert measurement.output_tokens > 0
    assert measurement.total_tokens == measurement.input_tokens + measurement.output_tokens


def test_usage_middleware_enables_supported_provider_stream_usage():
    class StreamUsageCapable:
        model_fields = {"stream_usage": object()}

    request = ModelRequest(
        model=StreamUsageCapable(),
        messages=[],
        runtime=SimpleNamespace(context={}),
        model_settings={"temperature": 0.1},
    )
    updated = middleware_module._enable_provider_stream_usage(request)
    assert updated.model_settings == {"temperature": 0.1, "stream_usage": True}


@pytest.mark.asyncio
async def test_async_usage_report_client_ignores_proxy_environment(monkeypatch):
    captured = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

    class _Client:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json):
            captured["request"] = (url, json)
            return _Response()

    monkeypatch.setenv("LUMEN_USAGE_REPORT_URL", "http://lumen_api/internal/usage")
    monkeypatch.setenv("LUMEN_USAGE_REPORT_RETRY_ATTEMPTS", "1")
    monkeypatch.setattr(accounting.httpx, "AsyncClient", _Client)

    await accounting._post_async({"usage_context": "signed", "event": {}})

    assert captured["kwargs"]["trust_env"] is False
    assert captured["kwargs"]["follow_redirects"] is False


def test_sync_usage_report_client_ignores_proxy_environment(monkeypatch):
    captured = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

    class _Client:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, json):
            captured["request"] = (url, json)
            return _Response()

    monkeypatch.setenv("LUMEN_USAGE_REPORT_URL", "http://lumen_api/internal/usage")
    monkeypatch.setenv("LUMEN_USAGE_REPORT_RETRY_ATTEMPTS", "1")
    monkeypatch.setattr(accounting.httpx, "Client", _Client)

    accounting._post_sync({"usage_context": "signed", "event": {}})

    assert captured["kwargs"]["trust_env"] is False
    assert captured["kwargs"]["follow_redirects"] is False


@pytest.mark.asyncio
async def test_reported_event_ids_are_bound_into_finalize(monkeypatch):
    envelopes = []

    async def post(envelope):
        envelopes.append(envelope)

    monkeypatch.setattr(accounting, "_post_async", post)
    context = {"usage_context": "signed-run-context"}
    response = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
    )
    first_id = await accounting.report_model_response_async(
        context=context,
        response=response,
        model=SimpleNamespace(model_name="model-a"),
        request_type="lead",
    )
    second_id = await accounting.report_model_response_async(
        context=context,
        response=response,
        model=SimpleNamespace(model_name="model-a"),
        request_type="summary",
    )
    assert await accounting.finalize_run_async(context)
    assert [item["event"]["kind"] for item in envelopes] == ["usage", "usage", "finalize"]
    assert envelopes[-1]["event"]["usage_event_ids"] == [first_id, second_id]


@pytest.mark.asyncio
async def test_failed_usage_report_prevents_reservation_release(monkeypatch):
    async def fail(_envelope):
        raise accounting.UsageReportingError("unavailable")

    monkeypatch.setattr(accounting, "_post_async", fail)
    context = {"usage_context": "signed-run-context"}
    with pytest.raises(accounting.UsageReportingError):
        await accounting.report_model_response_async(
            context=context,
            response=AIMessage(content="unreported"),
            model=SimpleNamespace(model="model-a"),
            request_type="subagent",
        )
    with pytest.raises(accounting.UsageReportingError, match="reservation retained"):
        await accounting.finalize_run_async(context)


@pytest.mark.asyncio
async def test_explicit_retention_prevents_release_for_still_running_work(monkeypatch):
    post = AsyncMock()
    monkeypatch.setattr(accounting, "_post_async", post)
    context = {"usage_context": "signed-run-context"}

    assert accounting.retain_run_reservation(context) is True

    with pytest.raises(accounting.UsageReportingError, match="reservation retained"):
        await accounting.finalize_run_async(context)
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_backend_call_without_admitted_usage_context(monkeypatch):
    post = AsyncMock()
    monkeypatch.setattr(accounting, "_post_async", post)
    event_id = await accounting.report_model_response_async(
        context={"thread_id": "local-only"},
        response=AIMessage(content="ok"),
        model=SimpleNamespace(model="model-a"),
        request_type="lead",
    )
    assert event_id is None
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_middleware_receives_non_persisted_runtime_context(monkeypatch):
    report = AsyncMock()
    finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(middleware_module, "report_model_response_async", report)
    monkeypatch.setattr(middleware_module, "finalize_run_async", finalize)
    model = FakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )
        ]
    )
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[UsageAccountingMiddleware(request_type="lead", finalize_run=True)],
    )

    await agent.ainvoke(
        {"messages": [HumanMessage(content="hello")]},
        context={"usage_context": "backend-signed-context"},
    )

    assert report.await_args.kwargs["context"] == {
        "usage_context": "backend-signed-context"
    }
    finalize.assert_awaited_once_with({"usage_context": "backend-signed-context"})


@pytest.mark.asyncio
async def test_summarization_call_is_accounted(monkeypatch):
    report = AsyncMock()
    monkeypatch.setattr(summary_module, "report_model_response_async", report)
    model = FakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="summary",
                usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            )
        ]
    )
    middleware = UsageSummarizationMiddleware(
        model=model,
        trigger=("messages", 2),
        keep=("messages", 1),
    )
    token = summary_module._current_usage_context.set(
        {"usage_context": "backend-signed-context"}
    )
    try:
        assert await middleware._acreate_summary(
            [HumanMessage(content="long history")]
        ) == "summary"
    finally:
        summary_module._current_usage_context.reset(token)

    assert report.await_args.kwargs["request_type"] == "summary"
    assert report.await_args.kwargs["context"] == {
        "usage_context": "backend-signed-context"
    }


@pytest.mark.asyncio
async def test_summarization_failure_does_not_expose_provider_error(
    caplog,
):
    secret = "provider-body-with-secret-marker"

    class FailingSummaryModel(FakeMessagesListChatModel):
        async def ainvoke(self, *_args, **_kwargs):
            raise RuntimeError(secret)

    model = FailingSummaryModel(responses=[AIMessage(content="unused")])
    middleware = UsageSummarizationMiddleware(
        model=model,
        trigger=("messages", 2),
        keep=("messages", 1),
    )

    result = await middleware._acreate_summary(
        [HumanMessage(content="long history")]
    )

    assert result == (
        "Summary generation failed; previous conversation remains available."
    )
    assert secret not in result
    assert secret not in caplog.text
