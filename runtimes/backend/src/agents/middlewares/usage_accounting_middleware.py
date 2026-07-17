"""Agent middleware that accounts every model turn and finalizes the lead run."""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import BaseMessage

from src.usage import (
    finalize_run_async,
    finalize_run_sync,
    report_model_response_async,
    report_model_response_sync,
)


def _request_messages(request: ModelRequest) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    if request.system_message is not None:
        messages.append(request.system_message)
    messages.extend(request.messages)
    return messages


def _enable_provider_stream_usage(request: ModelRequest) -> ModelRequest:
    model_fields = getattr(type(request.model), "model_fields", None)
    if not isinstance(model_fields, dict) or "stream_usage" not in model_fields:
        return request
    model_settings = dict(request.model_settings)
    model_settings["stream_usage"] = True
    return request.override(model_settings=model_settings)


class UsageAccountingMiddleware(AgentMiddleware):
    """Report model results under Runtime context, never browser-provided identity."""

    def __init__(self, *, request_type: str, finalize_run: bool = False):
        super().__init__()
        self._request_type = request_type
        self._finalize_run = finalize_run

    @override
    async def awrap_model_call(self, request: ModelRequest, handler):
        request = _enable_provider_stream_usage(request)
        response = await handler(request)
        await report_model_response_async(
            context=request.runtime.context,
            response=response,
            model=request.model,
            request_type=self._request_type,
            request_messages=_request_messages(request),
        )
        return response

    @override
    def wrap_model_call(self, request: ModelRequest, handler):
        request = _enable_provider_stream_usage(request)
        response = handler(request)
        report_model_response_sync(
            context=request.runtime.context,
            response=response,
            model=request.model,
            request_type=self._request_type,
            request_messages=_request_messages(request),
        )
        return response

    @override
    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        if self._finalize_run:
            await finalize_run_async(runtime.context)
        return None

    @override
    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        if self._finalize_run:
            finalize_run_sync(runtime.context)
        return None
