"""Measure provider responses and report them under a backend-issued run context."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import count_tokens_approximately

logger = logging.getLogger(__name__)

_USAGE_CONTEXT_KEY = "usage_context"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_RETRY_ATTEMPTS = 4
_RETRY_BASE_SECONDS = 0.2
_RUN_STATE_TTL_SECONDS = 24 * 60 * 60


class UsageReportingError(RuntimeError):
    """A measured provider call could not be durably accepted by the backend."""


@dataclass(frozen=True, slots=True)
class TokenMeasurement:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    source: str


@dataclass(slots=True)
class _RunReportState:
    usage_event_ids: list[str] = field(default_factory=list)
    reporting_failed: bool = False
    touched_at: float = field(default_factory=time.monotonic)


_states: dict[str, _RunReportState] = {}
_states_lock = threading.RLock()


def _context_token(context: Any) -> str | None:
    if not isinstance(context, Mapping):
        return None
    token = context.get(_USAGE_CONTEXT_KEY)
    if not isinstance(token, str):
        return None
    normalized = token.strip()
    return normalized or None


def _state_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _state_for(token: str) -> _RunReportState:
    key = _state_key(token)
    with _states_lock:
        cutoff = time.monotonic() - _RUN_STATE_TTL_SECONDS
        for stale_key, state in list(_states.items()):
            if state.touched_at < cutoff:
                _states.pop(stale_key, None)
        state = _states.setdefault(key, _RunReportState())
        state.touched_at = time.monotonic()
        return state


def _mark_failed(token: str) -> None:
    with _states_lock:
        _state_for(token).reporting_failed = True


def _record_event(token: str, event_id: str) -> None:
    with _states_lock:
        state = _state_for(token)
        if event_id not in state.usage_event_ids:
            state.usage_event_ids.append(event_id)


def _finalization_snapshot(token: str) -> list[str]:
    with _states_lock:
        state = _state_for(token)
        if state.reporting_failed:
            raise UsageReportingError(
                "A prior token usage event was not durably accepted; reservation retained"
            )
        return list(state.usage_event_ids)


def _clear_state(token: str) -> None:
    with _states_lock:
        _states.pop(_state_key(token), None)


def retain_run_reservation(context: Any) -> bool:
    """Fail closed when a run may still produce or have unreported usage."""

    token = _context_token(context)
    if token is None:
        return False
    _mark_failed(token)
    return True


def _as_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized >= 0 else None


def _measurement_from_mapping(mapping: Any) -> TokenMeasurement | None:
    if not isinstance(mapping, Mapping):
        return None

    nested_candidates = [mapping]
    for key in ("token_usage", "usage"):
        nested = mapping.get(key)
        if isinstance(nested, Mapping):
            nested_candidates.insert(0, nested)

    for candidate in nested_candidates:
        input_tokens = _as_non_negative_int(
            candidate.get("input_tokens", candidate.get("prompt_tokens"))
        )
        output_tokens = _as_non_negative_int(
            candidate.get("output_tokens", candidate.get("completion_tokens"))
        )
        total_tokens = _as_non_negative_int(candidate.get("total_tokens"))
        if input_tokens is None or output_tokens is None:
            continue
        computed_total = input_tokens + output_tokens
        return TokenMeasurement(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=max(total_tokens or 0, computed_total),
            source="response_metadata",
        )
    return None


def _response_messages(response: Any) -> list[BaseMessage]:
    if isinstance(response, BaseMessage):
        return [response]
    result = getattr(response, "result", None)
    if isinstance(result, BaseMessage):
        return [result]
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        return [item for item in result if isinstance(item, BaseMessage)]
    return []


def _reported_measurement(response: Any) -> TokenMeasurement | None:
    for message in _response_messages(response):
        usage_metadata = getattr(message, "usage_metadata", None)
        measurement = _measurement_from_mapping(usage_metadata)
        if measurement is not None:
            return TokenMeasurement(
                input_tokens=measurement.input_tokens,
                output_tokens=measurement.output_tokens,
                total_tokens=measurement.total_tokens,
                source="usage_metadata",
            )
        measurement = _measurement_from_mapping(
            getattr(message, "response_metadata", None)
        )
        if measurement is not None:
            return measurement

    generations = getattr(response, "generations", None)
    if isinstance(generations, Sequence):
        for generation_group in generations:
            if not isinstance(generation_group, Sequence):
                continue
            for generation in generation_group:
                message = getattr(generation, "message", None)
                if isinstance(message, BaseMessage):
                    measurement = _reported_measurement(message)
                    if measurement is not None:
                        return measurement
    return _measurement_from_mapping(getattr(response, "llm_output", None))


def _estimated_measurement(
    *,
    request_messages: Sequence[BaseMessage] | None,
    response: Any,
) -> TokenMeasurement:
    input_messages = list(request_messages or [])
    output_messages = _response_messages(response)
    try:
        input_tokens = int(count_tokens_approximately(input_messages))
    except Exception:
        input_tokens = max(sum(len(str(getattr(msg, "content", ""))) for msg in input_messages) // 4, 0)
    try:
        output_tokens = int(count_tokens_approximately(output_messages))
    except Exception:
        output_tokens = max(sum(len(str(getattr(msg, "content", ""))) for msg in output_messages) // 4, 0)
    return TokenMeasurement(
        input_tokens=max(input_tokens, 0),
        output_tokens=max(output_tokens, 0),
        total_tokens=max(input_tokens, 0) + max(output_tokens, 0),
        source="estimated",
    )


def measure_model_response(
    response: Any,
    *,
    request_messages: Sequence[BaseMessage] | None = None,
) -> TokenMeasurement:
    return _reported_measurement(response) or _estimated_measurement(
        request_messages=request_messages,
        response=response,
    )


def model_display_name(model: Any) -> str:
    for attribute in ("model_name", "model"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()[:255]
    return type(model).__name__[:255]


def _report_url() -> str:
    return str(os.getenv("LUMEN_USAGE_REPORT_URL") or "").strip()


def _timeout_seconds() -> float:
    try:
        return max(float(os.getenv("LUMEN_USAGE_REPORT_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)), 0.1)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def _retry_attempts() -> int:
    try:
        return max(int(os.getenv("LUMEN_USAGE_REPORT_RETRY_ATTEMPTS", _DEFAULT_RETRY_ATTEMPTS)), 1)
    except ValueError:
        return _DEFAULT_RETRY_ATTEMPTS


def _event_envelope(token: str, event: dict[str, Any]) -> dict[str, Any]:
    return {"usage_context": token, "event": event}


async def _post_async(envelope: dict[str, Any]) -> None:
    url = _report_url()
    if not url:
        raise UsageReportingError("LUMEN_USAGE_REPORT_URL is not configured")
    last_error: Exception | None = None
    async with httpx.AsyncClient(
        timeout=_timeout_seconds(),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for attempt in range(_retry_attempts()):
            try:
                response = await client.post(url, json=envelope)
                response.raise_for_status()
                return
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt + 1 < _retry_attempts():
                    await asyncio.sleep(_RETRY_BASE_SECONDS * (2**attempt))
    raise UsageReportingError("Backend did not durably accept token usage") from last_error


def _post_sync(envelope: dict[str, Any]) -> None:
    url = _report_url()
    if not url:
        raise UsageReportingError("LUMEN_USAGE_REPORT_URL is not configured")
    last_error: Exception | None = None
    with httpx.Client(
        timeout=_timeout_seconds(),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for attempt in range(_retry_attempts()):
            try:
                response = client.post(url, json=envelope)
                response.raise_for_status()
                return
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt + 1 < _retry_attempts():
                    time.sleep(_RETRY_BASE_SECONDS * (2**attempt))
    raise UsageReportingError("Backend did not durably accept token usage") from last_error


def _usage_event(
    *,
    event_id: str,
    measurement: TokenMeasurement,
    model_name: str,
    request_type: str,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "kind": "usage",
        "occurred_at": datetime.now(UTC).isoformat(),
        "model_name": model_name[:255],
        "request_type": request_type[:64],
        "input_tokens": measurement.input_tokens,
        "output_tokens": measurement.output_tokens,
        "total_tokens": measurement.total_tokens,
        "usage_source": measurement.source,
        "usage_event_ids": [],
    }


async def report_model_response_async(
    *,
    context: Any,
    response: Any,
    model: Any,
    request_type: str,
    request_messages: Sequence[BaseMessage] | None = None,
) -> str | None:
    token = _context_token(context)
    if token is None:
        return None
    event_id = str(uuid.uuid4())
    event = _usage_event(
        event_id=event_id,
        measurement=measure_model_response(response, request_messages=request_messages),
        model_name=model_display_name(model),
        request_type=request_type,
    )
    try:
        await _post_async(_event_envelope(token, event))
    except Exception:
        _mark_failed(token)
        raise
    _record_event(token, event_id)
    return event_id


def report_model_response_sync(
    *,
    context: Any,
    response: Any,
    model: Any,
    request_type: str,
    request_messages: Sequence[BaseMessage] | None = None,
) -> str | None:
    token = _context_token(context)
    if token is None:
        return None
    event_id = str(uuid.uuid4())
    event = _usage_event(
        event_id=event_id,
        measurement=measure_model_response(response, request_messages=request_messages),
        model_name=model_display_name(model),
        request_type=request_type,
    )
    try:
        _post_sync(_event_envelope(token, event))
    except Exception:
        _mark_failed(token)
        raise
    _record_event(token, event_id)
    return event_id


def _finalize_event(usage_event_ids: list[str]) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "kind": "finalize",
        "occurred_at": datetime.now(UTC).isoformat(),
        "usage_event_ids": usage_event_ids,
    }


async def finalize_run_async(context: Any) -> bool:
    token = _context_token(context)
    if token is None:
        return False
    event_ids = _finalization_snapshot(token)
    try:
        await _post_async(_event_envelope(token, _finalize_event(event_ids)))
    except Exception:
        _mark_failed(token)
        raise
    _clear_state(token)
    return True


def finalize_run_sync(context: Any) -> bool:
    token = _context_token(context)
    if token is None:
        return False
    event_ids = _finalization_snapshot(token)
    try:
        _post_sync(_event_envelope(token, _finalize_event(event_ids)))
    except Exception:
        _mark_failed(token)
        raise
    _clear_state(token)
    return True
