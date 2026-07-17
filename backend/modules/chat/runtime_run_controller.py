"""Authenticated, session-scoped proxy for LangGraph run operations."""

from __future__ import annotations

from copy import deepcopy
import json
import logging
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from config.database import get_db
from config.redis import get_redis_client
from middlewares.auth import AuthenticatedIdentity, get_current_chat_identity
from modules.chat.runtime_memory_scope import derive_runtime_memory_scope
from modules.chat.runtime_thread_guard import runtime_thread_guard
from schemas.token_usage import RuntimeTokenUsageEnvelope
from services.token_quota_service import (
    TokenQuotaService,
    build_quota_exceeded_error,
)
from utils.token_usage_context import InvalidUsageContext
from utils.token_usage_queue import get_token_usage_producer


router = APIRouter(prefix="/chat-runtime", tags=["Chat Runtime"])
logger = logging.getLogger(__name__)

_STREAM_MODES = ["messages-tuple", "values", "custom"]
_PASSTHROUGH_CONTEXT_KEYS = {
    "reasoning_effort",
}


def _log_internal_failure(stage: str, error: BaseException) -> None:
    logger.error(
        "runtime_run stage=%s error_type=%s",
        stage,
        type(error).__name__,
    )


def _context_bool(context: dict[str, Any], key: str, default: bool = False) -> bool:
    value = context.get(key)
    return value if isinstance(value, bool) else default


def _create_chat_service(db: AsyncSession):
    from modules.chat.repositories.chat_repository import ChatRepository
    from modules.chat.services.chat_service import ChatService

    return ChatService(ChatRepository(db))


def _create_model_config_service(db: AsyncSession):
    from modules.model_config.services.model_config_service import ModelConfigService

    return ModelConfigService(db)


def _create_runtime_knowledge_scope_service(db: AsyncSession):
    from modules.chat.services.runtime_knowledge_scope_service import (
        RuntimeKnowledgeScopeService,
    )

    return RuntimeKnowledgeScopeService(db)


def _get_runtime_service():
    from modules.chat.services.insight_runtime_service import insight_runtime_service

    return insight_runtime_service


def _get_thread_materialization_service():
    from modules.chat.services.thread_materialization_service import (
        thread_materialization_service,
    )

    return thread_materialization_service


async def _create_quota_service(db: AsyncSession) -> TokenQuotaService:
    return TokenQuotaService(await get_redis_client(), db)


async def _resolve_owned_session(
    *,
    session_id: UUID,
    identity: AuthenticatedIdentity,
    db: AsyncSession,
) -> tuple[Any, str]:
    session = await _create_chat_service(db).get_session(session_id, identity.user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    runtime_service = _get_runtime_service()
    session_config = dict(getattr(session, "config", {}) or {})
    thread_id = runtime_service.build_thread_id(
        str(session_config.get("threadId") or session_id).strip()
    )
    return session, thread_id


def _knowledge_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


async def _validate_run_knowledge_scope(
    *,
    session: Any,
    thread_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession,
) -> dict[str, Any]:
    session_config = dict(getattr(session, "config", {}) or {})
    if "runtimeKnowledgeFiles" not in session_config:
        raise _knowledge_error(
            409,
            "RUNTIME_PREPARATION_REQUIRED",
            "Prepare the Runtime before starting a run",
        )

    scope_service = _create_runtime_knowledge_scope_service(db)
    try:
        scope = await scope_service.resolve_current_scope(
            session_config=session_config,
            current_user=identity.user,
        )
        manifest = scope_service.validate_manifest(
            scope=scope,
            raw_manifest=session_config.get("runtimeKnowledgeFiles"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log_internal_failure("knowledge_scope", exc)
        raise _knowledge_error(
            503,
            "KNOWLEDGE_VALIDATION_UNAVAILABLE",
            "Knowledge authorization could not be validated",
        ) from exc

    runtime_service = _get_runtime_service()
    try:
        uploads = await runtime_service.list_thread_uploads(thread_id)
    except Exception as exc:
        _log_internal_failure("knowledge_list", exc)
        raise _knowledge_error(
            503,
            "KNOWLEDGE_VALIDATION_UNAVAILABLE",
            "Runtime knowledge files could not be validated",
        ) from exc

    expected_filenames = {item.thread_filename for item in manifest}
    managed_filenames = {
        str(item.get("filename", "")).strip()
        for item in uploads
        if str(item.get("filename", "")).strip().lower().startswith("kb__")
    }
    if managed_filenames != expected_filenames:
        raise _knowledge_error(
            409,
            "RUNTIME_MATERIALIZATION_STALE",
            "Runtime knowledge files do not match the prepared manifest",
        )

    for prepared_file in manifest:
        try:
            integrity = await runtime_service.get_thread_upload_integrity(
                thread_id=thread_id,
                filename=prepared_file.thread_filename,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise _knowledge_error(
                    409,
                    "RUNTIME_MATERIALIZATION_STALE",
                    "A prepared Runtime knowledge file is missing",
                ) from exc
            _log_internal_failure("knowledge_integrity_status", exc)
            raise _knowledge_error(
                503,
                "KNOWLEDGE_VALIDATION_UNAVAILABLE",
                "Runtime knowledge files could not be validated",
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            _log_internal_failure("knowledge_integrity", exc)
            raise _knowledge_error(
                503,
                "KNOWLEDGE_VALIDATION_UNAVAILABLE",
                "Runtime knowledge files could not be validated",
            ) from exc

        if not isinstance(integrity, dict):
            raise _knowledge_error(
                503,
                "KNOWLEDGE_VALIDATION_UNAVAILABLE",
                "Runtime returned invalid knowledge file metadata",
            )
        try:
            actual_size = int(integrity.get("size"))
        except (TypeError, ValueError):
            actual_size = -1
        if (
            str(integrity.get("filename", "")).strip() != prepared_file.thread_filename
            or actual_size != prepared_file.size_bytes
            or str(integrity.get("sha256", "")).strip().lower()
            != prepared_file.content_sha256
        ):
            raise _knowledge_error(
                409,
                "RUNTIME_MATERIALIZATION_STALE",
                "A prepared Runtime knowledge file has changed",
            )

    document_ids = list(scope.requested_doc_ids)
    server_context: dict[str, Any] = {
        "knowledge_scope": {
            "mode": scope.scope_mode,
            "kb_ids": list(scope.kb_ids),
            "doc_ids": document_ids,
        },
        "kb_ids": list(scope.kb_ids),
        "doc_ids": document_ids,
    }
    if len(scope.kb_ids) == 1:
        server_context["kb_id"] = scope.kb_ids[0]
    return server_context


async def _build_authorized_run_payload(
    *,
    payload: dict[str, Any],
    session: Any,
    thread_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession,
) -> dict[str, Any]:
    input_payload = payload.get("input")
    if not isinstance(input_payload, dict) or not isinstance(
        input_payload.get("messages"), list
    ):
        raise HTTPException(status_code=422, detail="Run input.messages must be a list")

    knowledge_context = await _validate_run_knowledge_scope(
        session=session,
        thread_id=thread_id,
        identity=identity,
        db=db,
    )
    runtime_service = _get_runtime_service()
    incoming_context = payload.get("context")
    if not isinstance(incoming_context, dict):
        incoming_context = {}
    session_config = dict(getattr(session, "config", {}) or {})

    requested_model_name = (
        str(
            incoming_context.get("model_name") or session_config.get("modelName") or ""
        ).strip()
        or None
    )
    model_resolution = await _create_model_config_service(db).resolve_selected_model(
        user_id=identity.user.id,
        selected_model_name=requested_model_name,
        runtime_models=await runtime_service.list_runtime_models(),
        thread_id=thread_id,
    )
    runtime_model_name = str(model_resolution["runtime_model_name"]).strip()

    thinking_enabled = _context_bool(
        incoming_context,
        "thinking_enabled",
        bool(session_config.get("deepThinking", False)),
    )
    is_plan_mode = _context_bool(
        incoming_context,
        "is_plan_mode",
        str(session_config.get("uiMode") or "normal").strip().lower() == "plan",
    )

    secured = runtime_service.build_run_request_template(
        thread_id=thread_id,
        assistant_id=await runtime_service.resolve_assistant_id(),
        model_name=runtime_model_name,
        thinking_enabled=thinking_enabled,
        is_plan_mode=is_plan_mode,
        subagent_enabled=_context_bool(incoming_context, "subagent_enabled"),
        disable_model_streaming=_context_bool(
            incoming_context, "disable_model_streaming"
        ),
    )
    secured["input"] = {"messages": deepcopy(input_payload["messages"])}

    secured_context = dict(secured["context"])
    for key in _PASSTHROUGH_CONTEXT_KEYS:
        if key in incoming_context:
            secured_context[key] = deepcopy(incoming_context[key])
    secured_context.update(knowledge_context)

    # Long-term memory is a server-owned authorization partition. Never accept
    # a caller-supplied scope, and keep persistent memory disabled for guests.
    secured_context.pop("memory_scope", None)
    if not identity.is_guest:
        secured_context["memory_scope"] = derive_runtime_memory_scope(identity.user.id)

    dynamic_model_token = model_resolution.get("dynamic_model_token")
    if dynamic_model_token:
        secured_context["dynamic_model_token"] = dynamic_model_token
    secured["context"] = secured_context
    return secured


def _stream_timeout(runtime_service: Any) -> httpx.Timeout:
    timeout = float(runtime_service.request_timeout_seconds)
    return httpx.Timeout(connect=timeout, read=None, write=timeout, pool=timeout)


async def _close_upstream(response: httpx.Response, client: httpx.AsyncClient) -> None:
    await response.aclose()
    await client.aclose()


async def _proxy_stream(
    *,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Response:
    runtime_service = _get_runtime_service()
    client = httpx.AsyncClient(
        timeout=_stream_timeout(runtime_service),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        request = client.build_request(method, url, json=payload, params=params)
        upstream = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=502, detail="Runtime service is unavailable"
        ) from exc

    if upstream.status_code >= 400:
        content = await upstream.aread()
        content_type = upstream.headers.get("content-type", "application/json")
        await _close_upstream(upstream, client)
        return Response(
            content=content,
            status_code=upstream.status_code,
            headers={"Content-Type": content_type},
        )

    headers = {
        "Cache-Control": upstream.headers.get("cache-control", "no-cache"),
        "Content-Type": upstream.headers.get("content-type", "text/event-stream"),
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(_close_upstream, upstream, client),
    )


async def _proxy_buffered(
    *,
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
) -> Response:
    runtime_service = _get_runtime_service()
    try:
        async with httpx.AsyncClient(
            timeout=runtime_service.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            upstream = await client.request(method, url, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Runtime service is unavailable"
        ) from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={
            "Content-Type": upstream.headers.get("content-type", "application/json")
        },
    )


async def _admit_stream_session_run(
    *,
    session_id: UUID,
    payload: dict[str, Any],
    session: Any,
    thread_id: str,
    identity: AuthenticatedIdentity,
    db: AsyncSession,
) -> Response:
    secured_payload = await _build_authorized_run_payload(
        payload=payload,
        session=session,
        thread_id=thread_id,
        identity=identity,
        db=db,
    )
    try:
        quota_service = await _create_quota_service(db)
        reservation = await quota_service.reserve_run(
            user=identity.user,
            session_id=session_id,
        )
    except Exception as exc:
        _log_internal_failure("quota_reserve", exc)
        return JSONResponse(
            status_code=503,
            content={
                "code": "TOKEN_ACCOUNTING_UNAVAILABLE",
                "message": "Token accounting is temporarily unavailable",
            },
        )

    if not reservation.allowed:
        return JSONResponse(
            status_code=429,
            content=build_quota_exceeded_error(reservation),
        )
    if not reservation.usage_context:
        await quota_service.release(reservation, user_id=identity.user.id)
        return JSONResponse(
            status_code=503,
            content={
                "code": "TOKEN_ACCOUNTING_UNAVAILABLE",
                "message": "Token accounting is temporarily unavailable",
            },
        )
    secured_payload["context"]["usage_context"] = reservation.usage_context

    runtime_service = _get_runtime_service()
    response = await _proxy_stream(
        method="POST",
        url=f"{runtime_service.langgraph_url}/threads/{thread_id}/runs/stream",
        payload=secured_payload,
    )
    # A concrete upstream rejection means no run was admitted. Transport errors are
    # ambiguous and deliberately keep the reservation until its crash-recovery TTL.
    if response.status_code >= 400:
        await quota_service.release(reservation, user_id=identity.user.id)
    return response


@router.post("/sessions/{session_id}/runs/stream")
async def stream_session_run(
    session_id: UUID,
    payload: dict[str, Any] = Body(...),
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _session, thread_id = await _resolve_owned_session(
        session_id=session_id,
        identity=identity,
        db=db,
    )
    materialization_service = _get_thread_materialization_service()
    async with runtime_thread_guard(materialization_service, thread_id):
        session, refreshed_thread_id = await _resolve_owned_session(
            session_id=session_id,
            identity=identity,
            db=db,
        )
        if refreshed_thread_id != thread_id:
            raise _knowledge_error(
                409,
                "RUNTIME_PREPARATION_REQUIRED",
                "The session Runtime thread changed during run admission",
            )
        return await _admit_stream_session_run(
            session_id=session_id,
            payload=payload,
            session=session,
            thread_id=thread_id,
            identity=identity,
            db=db,
        )


@router.get("/quota")
async def get_runtime_quota(
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Return current-cycle committed usage and outstanding reservations."""

    try:
        service = await _create_quota_service(db)
        snapshot = await service.get_snapshot(user=identity.user)
    except Exception as exc:
        _log_internal_failure("quota_snapshot", exc)
        raise HTTPException(
            status_code=503,
            detail="Token accounting is temporarily unavailable",
        ) from exc
    return snapshot.to_details()


@router.post("/internal/token-usage/events", status_code=202)
async def accept_runtime_token_usage(
    envelope: RuntimeTokenUsageEnvelope,
) -> dict[str, str]:
    """Verify a Runtime-only credential before durably accepting an event."""

    producer = get_token_usage_producer()
    if producer is None:
        raise HTTPException(status_code=503, detail="Token usage queue is unavailable")
    try:
        stream_id = await producer.enqueue(envelope)
    except InvalidUsageContext as exc:
        raise HTTPException(status_code=401, detail="Invalid usage context") from exc
    except Exception as exc:
        _log_internal_failure("usage_enqueue", exc)
        raise HTTPException(
            status_code=503, detail="Token usage queue is unavailable"
        ) from exc
    return {"event_id": str(envelope.event.event_id), "stream_id": str(stream_id)}


@router.get("/sessions/{session_id}/runs")
async def list_session_runs(
    session_id: UUID,
    limit: int = Query(default=1, ge=1, le=20),
    status: Literal["running", "pending"] = Query(...),
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _session, thread_id = await _resolve_owned_session(
        session_id=session_id,
        identity=identity,
        db=db,
    )
    runtime_service = _get_runtime_service()
    return await _proxy_buffered(
        method="GET",
        url=f"{runtime_service.langgraph_url}/threads/{thread_id}/runs",
        params={"limit": limit, "status": status},
    )


@router.get("/sessions/{session_id}/runs/{run_id}/stream")
async def join_session_run(
    session_id: UUID,
    run_id: UUID,
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _session, thread_id = await _resolve_owned_session(
        session_id=session_id,
        identity=identity,
        db=db,
    )
    runtime_service = _get_runtime_service()
    return await _proxy_stream(
        method="GET",
        url=f"{runtime_service.langgraph_url}/threads/{thread_id}/runs/{run_id}/stream",
        params={
            "stream_mode": json.dumps(_STREAM_MODES, separators=(",", ":")),
            "cancel_on_disconnect": "false",
        },
    )


@router.post("/sessions/{session_id}/runs/{run_id}/cancel")
async def cancel_session_run(
    session_id: UUID,
    run_id: UUID,
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _session, thread_id = await _resolve_owned_session(
        session_id=session_id,
        identity=identity,
        db=db,
    )
    runtime_service = _get_runtime_service()
    return await _proxy_buffered(
        method="POST",
        url=f"{runtime_service.langgraph_url}/threads/{thread_id}/runs/{run_id}/cancel",
        params={"action": "interrupt", "wait": 0},
    )
