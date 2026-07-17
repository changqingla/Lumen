import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest
from fastapi import HTTPException

from middlewares.auth import AuthenticatedIdentity
from modules.chat import runtime_run_controller
from modules.chat.services.thread_materialization_service import (
    ThreadMaterializationLockTimeout,
)
from services.token_quota_service import QuotaReservation, QuotaSnapshot


def _identity(user_id):
    return AuthenticatedIdentity(user=SimpleNamespace(id=user_id), is_guest=False)


@asynccontextmanager
async def _test_thread_guard(_thread_id: str):
    yield


@pytest.fixture(autouse=True)
def _inject_test_thread_guard(monkeypatch):
    monkeypatch.setattr(
        runtime_run_controller,
        "_get_thread_materialization_service",
        lambda: SimpleNamespace(thread_guard=_test_thread_guard),
    )


def _runtime_service():
    def build_template(**kwargs):
        return {
            "assistant_id": kwargs["assistant_id"],
            "on_disconnect": "continue",
            "multitask_strategy": "reject",
            "stream_mode": ["messages-tuple", "values", "custom"],
            "context": {
                "thread_id": kwargs["thread_id"],
                "model_name": kwargs["model_name"],
                "thinking_enabled": kwargs["thinking_enabled"],
                "is_plan_mode": kwargs["is_plan_mode"],
                "subagent_enabled": kwargs["subagent_enabled"],
                "disable_model_streaming": kwargs["disable_model_streaming"],
            },
            "config": {"recursion_limit": 300},
            "input": {"messages": []},
        }

    return SimpleNamespace(
        build_thread_id=lambda value: value,
        list_runtime_models=AsyncMock(return_value=[]),
        resolve_assistant_id=AsyncMock(return_value="trusted-assistant"),
        build_run_request_template=build_template,
        langgraph_url="http://langgraph",
        request_timeout_seconds=10,
    )


@pytest.mark.asyncio
async def test_runtime_thread_guard_maps_lock_failure_to_retryable_error():
    @asynccontextmanager
    async def failed_guard(_thread_id: str):
        raise ThreadMaterializationLockTimeout("busy")
        yield  # pragma: no cover

    with pytest.raises(HTTPException) as exc_info:
        async with runtime_run_controller.runtime_thread_guard(
            SimpleNamespace(thread_guard=failed_guard),
            "thread-1",
        ):
            pass

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "THREAD_GUARD_UNAVAILABLE"


@pytest.mark.asyncio
async def test_build_authorized_run_payload_replaces_client_control_fields(monkeypatch):
    user_id = uuid4()
    runtime_service = _runtime_service()
    model_service = SimpleNamespace(
        resolve_selected_model=AsyncMock(
            return_value={
                "runtime_model_name": "user-model:trusted",
                "dynamic_model_token": "server-issued-token",
            }
        )
    )
    monkeypatch.setattr(runtime_run_controller, "_get_runtime_service", lambda: runtime_service)
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_model_config_service",
        lambda _db: model_service,
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_validate_run_knowledge_scope",
        AsyncMock(
            return_value={
                "knowledge_scope": {
                    "mode": "explicit",
                    "kb_ids": ["trusted-kb"],
                    "doc_ids": ["trusted-doc"],
                },
                "kb_ids": ["trusted-kb"],
                "kb_id": "trusted-kb",
                "doc_ids": ["trusted-doc"],
            }
        ),
    )

    payload = {
        "assistant_id": "attacker-assistant",
        "input": {
            "messages": [{"role": "user", "content": "hello"}],
            "artifacts": ["should-not-be-injected"],
        },
        "context": {
            "thread_id": "another-users-thread",
            "model_name": "user-model:requested",
            "dynamic_model_token": "attacker-token",
            "usage_context": "attacker-usage-context",
            "reasoning_effort": "high",
            "kb_id": "attacker-kb",
            "doc_ids": ["attacker-doc"],
            "unknown_secret": "drop-me",
        },
        "config": {"recursion_limit": 999999},
    }

    secured = await runtime_run_controller._build_authorized_run_payload(
        payload=payload,
        session=SimpleNamespace(config={"modelName": "fallback-model"}),
        thread_id="owned-thread",
        identity=_identity(user_id),
        db=object(),
    )

    assert secured["assistant_id"] == "trusted-assistant"
    assert secured["input"] == {"messages": [{"role": "user", "content": "hello"}]}
    assert secured["config"] == {"recursion_limit": 300}
    assert secured["context"]["thread_id"] == "owned-thread"
    assert secured["context"]["model_name"] == "user-model:trusted"
    assert secured["context"]["dynamic_model_token"] == "server-issued-token"
    assert secured["context"]["reasoning_effort"] == "high"
    assert secured["context"]["kb_id"] == "trusted-kb"
    assert secured["context"]["kb_ids"] == ["trusted-kb"]
    assert secured["context"]["doc_ids"] == ["trusted-doc"]
    assert "unknown_secret" not in secured["context"]
    assert "usage_context" not in secured["context"]
    model_service.resolve_selected_model.assert_awaited_once_with(
        user_id=user_id,
        selected_model_name="user-model:requested",
        runtime_models=[],
        thread_id="owned-thread",
    )


@pytest.mark.asyncio
async def test_build_authorized_run_payload_requires_messages(monkeypatch):
    monkeypatch.setattr(runtime_run_controller, "_get_runtime_service", _runtime_service)

    with pytest.raises(HTTPException) as exc_info:
        await runtime_run_controller._build_authorized_run_payload(
            payload={"input": {"messages": "not-a-list"}},
            session=SimpleNamespace(config={}),
            thread_id="owned-thread",
            identity=_identity(uuid4()),
            db=object(),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_resolve_owned_session_rejects_non_owner(monkeypatch):
    chat_service = SimpleNamespace(get_session=AsyncMock(return_value=None))
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_chat_service",
        lambda _db: chat_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtime_run_controller._resolve_owned_session(
            session_id=uuid4(),
            identity=_identity(uuid4()),
            db=object(),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_run_knowledge_scope_requires_prepare():
    with pytest.raises(HTTPException) as exc_info:
        await runtime_run_controller._validate_run_knowledge_scope(
            session=SimpleNamespace(config={"kbIds": [], "docIds": []}),
            thread_id="owned-thread",
            identity=_identity(uuid4()),
            db=object(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_PREPARATION_REQUIRED"


@pytest.mark.asyncio
async def test_run_knowledge_scope_failure_is_not_logged_or_returned(
    monkeypatch,
    caplog,
):
    marker = "private-knowledge-provider-detail"
    scope_service = SimpleNamespace(
        resolve_current_scope=AsyncMock(side_effect=RuntimeError(marker)),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_runtime_knowledge_scope_service",
        lambda _db: scope_service,
    )

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(HTTPException) as exc_info,
    ):
        await runtime_run_controller._validate_run_knowledge_scope(
            session=SimpleNamespace(config={"runtimeKnowledgeFiles": []}),
            thread_id="owned-thread",
            identity=_identity(uuid4()),
            db=object(),
        )

    assert exc_info.value.status_code == 503
    assert marker not in str(exc_info.value.detail)
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_run_knowledge_scope_validates_runtime_bytes(monkeypatch):
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    digest = "a" * 64
    filename = f"kb__{kb_id}__{doc_id}__{digest[:16]}__paper.md"
    scope = SimpleNamespace(
        scope_mode="explicit",
        kb_ids=(kb_id,),
        requested_doc_ids=(doc_id,),
        documents=(SimpleNamespace(doc_id=doc_id),),
    )
    manifest = (
        SimpleNamespace(
            thread_filename=filename,
            size_bytes=12,
            content_sha256=digest,
        ),
    )
    scope_service = SimpleNamespace(
        resolve_current_scope=AsyncMock(return_value=scope),
        validate_manifest=MagicMock(return_value=manifest),
    )
    runtime_service = SimpleNamespace(
        list_thread_uploads=AsyncMock(return_value=[{"filename": filename, "size": 12}]),
        get_thread_upload_integrity=AsyncMock(
            return_value={"filename": filename, "size": 12, "sha256": digest}
        ),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_runtime_knowledge_scope_service",
        lambda _db: scope_service,
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_get_runtime_service",
        lambda: runtime_service,
    )

    result = await runtime_run_controller._validate_run_knowledge_scope(
        session=SimpleNamespace(
            config={"kbIds": [kb_id], "docIds": [doc_id], "runtimeKnowledgeFiles": []}
        ),
        thread_id="owned-thread",
        identity=_identity(uuid4()),
        db=object(),
    )

    assert result == {
        "knowledge_scope": {
            "mode": "explicit",
            "kb_ids": [kb_id],
            "doc_ids": [doc_id],
        },
        "kb_ids": [kb_id],
        "kb_id": kb_id,
        "doc_ids": [doc_id],
    }
    runtime_service.get_thread_upload_integrity.assert_awaited_once_with(
        thread_id="owned-thread",
        filename=filename,
    )


@pytest.mark.asyncio
async def test_run_knowledge_scope_rejects_tampered_runtime_file(monkeypatch):
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    digest = "a" * 64
    filename = f"kb__{kb_id}__{doc_id}__{digest[:16]}__paper.md"
    scope_service = SimpleNamespace(
        resolve_current_scope=AsyncMock(
            return_value=SimpleNamespace(
                scope_mode="all_materialized",
                kb_ids=(kb_id,),
                requested_doc_ids=(),
                documents=(SimpleNamespace(doc_id=doc_id),),
            )
        ),
        validate_manifest=MagicMock(
            return_value=(
                SimpleNamespace(
                    thread_filename=filename,
                    size_bytes=12,
                    content_sha256=digest,
                ),
            )
        ),
    )
    runtime_service = SimpleNamespace(
        list_thread_uploads=AsyncMock(return_value=[{"filename": filename, "size": 12}]),
        get_thread_upload_integrity=AsyncMock(
            return_value={"filename": filename, "size": 12, "sha256": "b" * 64}
        ),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_runtime_knowledge_scope_service",
        lambda _db: scope_service,
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_get_runtime_service",
        lambda: runtime_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtime_run_controller._validate_run_knowledge_scope(
            session=SimpleNamespace(
                config={"kbIds": [kb_id], "docIds": [], "runtimeKnowledgeFiles": []}
            ),
            thread_id="owned-thread",
            identity=_identity(uuid4()),
            db=object(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_MATERIALIZATION_STALE"


@pytest.mark.asyncio
async def test_stream_session_run_uses_owned_thread_and_secured_payload(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    session = SimpleNamespace(config={"threadId": "owned-thread"})
    secured_payload = {"input": {"messages": []}, "context": {"thread_id": "owned-thread"}}
    proxy_response = MagicMock(status_code=200)
    runtime_service = _runtime_service()
    resolve_owned = AsyncMock(return_value=(session, "owned-thread"))
    build_authorized = AsyncMock(return_value=secured_payload)
    proxy_stream = AsyncMock(return_value=proxy_response)
    reservation = QuotaReservation(
        allowed=True,
        snapshot=QuotaSnapshot(
            user_level="basic",
            used_tokens=0,
            pending_reserved_tokens=0,
            quota_limit=1_000_000,
            reset_date=datetime.now(timezone.utc),
        ),
        reservation_id=uuid4(),
        window_start=datetime.now(timezone.utc),
        usage_context="server-signed-usage-context",
    )
    quota_service = SimpleNamespace(
        reserve_run=AsyncMock(return_value=reservation),
        release=AsyncMock(return_value=0),
    )
    monkeypatch.setattr(runtime_run_controller, "_resolve_owned_session", resolve_owned)
    monkeypatch.setattr(runtime_run_controller, "_build_authorized_run_payload", build_authorized)
    monkeypatch.setattr(runtime_run_controller, "_get_runtime_service", lambda: runtime_service)
    monkeypatch.setattr(runtime_run_controller, "_proxy_stream", proxy_stream)
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_quota_service",
        AsyncMock(return_value=quota_service),
    )

    response = await runtime_run_controller.stream_session_run(
        session_id=session_id,
        payload={"context": {"thread_id": "attacker-thread"}, "input": {"messages": []}},
        identity=_identity(user_id),
        db=object(),
    )

    assert response is proxy_response
    assert secured_payload["context"]["usage_context"] == "server-signed-usage-context"
    proxy_stream.assert_awaited_once_with(
        method="POST",
        url="http://langgraph/threads/owned-thread/runs/stream",
        payload=secured_payload,
    )
    quota_service.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_knowledge_scope_blocks_quota_reservation_and_upstream(monkeypatch):
    build_authorized = AsyncMock(
        side_effect=HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "RUNTIME_KNOWLEDGE_STALE",
                    "message": "stale",
                }
            },
        )
    )
    quota_factory = AsyncMock()
    proxy_stream = AsyncMock()
    monkeypatch.setattr(
        runtime_run_controller,
        "_resolve_owned_session",
        AsyncMock(return_value=(SimpleNamespace(config={}), "owned-thread")),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_build_authorized_run_payload",
        build_authorized,
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_quota_service",
        quota_factory,
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_proxy_stream",
        proxy_stream,
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtime_run_controller.stream_session_run(
            session_id=uuid4(),
            payload={"input": {"messages": []}},
            identity=_identity(uuid4()),
            db=object(),
        )

    assert exc_info.value.status_code == 409
    quota_factory.assert_not_awaited()
    proxy_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_admission_holds_thread_guard_until_upstream_accepts(monkeypatch):
    events = []
    response = MagicMock(status_code=200)

    @asynccontextmanager
    async def thread_guard(thread_id):
        events.append(("guard-enter", thread_id))
        try:
            yield
        finally:
            events.append(("guard-exit", thread_id))

    async def admit(**kwargs):
        events.append(("admit", kwargs["thread_id"]))
        return response

    monkeypatch.setattr(
        runtime_run_controller,
        "_resolve_owned_session",
        AsyncMock(return_value=(SimpleNamespace(config={}), "owned-thread")),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_get_thread_materialization_service",
        lambda: SimpleNamespace(thread_guard=thread_guard),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_admit_stream_session_run",
        AsyncMock(side_effect=admit),
    )

    result = await runtime_run_controller.stream_session_run(
        session_id=uuid4(),
        payload={"input": {"messages": []}},
        identity=_identity(uuid4()),
        db=object(),
    )

    assert result is response
    assert events == [
        ("guard-enter", "owned-thread"),
        ("admit", "owned-thread"),
        ("guard-exit", "owned-thread"),
    ]


@pytest.mark.asyncio
async def test_upstream_rejection_releases_unused_reservation(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    reservation = QuotaReservation(
        allowed=True,
        snapshot=QuotaSnapshot(
            user_level="basic",
            used_tokens=0,
            pending_reserved_tokens=0,
            quota_limit=1_000_000,
            reset_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ),
        reservation_id=uuid4(),
        window_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        usage_context="server-signed-usage-context",
    )
    quota_service = SimpleNamespace(
        reserve_run=AsyncMock(return_value=reservation),
        release=AsyncMock(return_value=250_000),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_resolve_owned_session",
        AsyncMock(return_value=(SimpleNamespace(config={}), "owned-thread")),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_build_authorized_run_payload",
        AsyncMock(return_value={"input": {"messages": []}, "context": {}}),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_quota_service",
        AsyncMock(return_value=quota_service),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_get_runtime_service",
        _runtime_service,
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_proxy_stream",
        AsyncMock(return_value=MagicMock(status_code=409)),
    )

    response = await runtime_run_controller.stream_session_run(
        session_id=session_id,
        payload={"input": {"messages": []}},
        identity=_identity(user_id),
        db=object(),
    )

    assert response.status_code == 409
    quota_service.release.assert_awaited_once_with(
        reservation,
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_stream_session_run_returns_structured_quota_error(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    snapshot = QuotaSnapshot(
        user_level="basic",
        used_tokens=900_000,
        pending_reserved_tokens=100_000,
        quota_limit=1_000_000,
        reset_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    quota_service = SimpleNamespace(
        reserve_run=AsyncMock(
            return_value=QuotaReservation(allowed=False, snapshot=snapshot)
        )
    )
    proxy_stream = AsyncMock()
    monkeypatch.setattr(
        runtime_run_controller,
        "_resolve_owned_session",
        AsyncMock(return_value=(SimpleNamespace(config={}), "owned-thread")),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_build_authorized_run_payload",
        AsyncMock(return_value={"input": {"messages": []}, "context": {}}),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_quota_service",
        AsyncMock(return_value=quota_service),
    )
    monkeypatch.setattr(runtime_run_controller, "_proxy_stream", proxy_stream)

    response = await runtime_run_controller.stream_session_run(
        session_id=session_id,
        payload={"input": {"messages": []}},
        identity=_identity(user_id),
        db=object(),
    )

    assert response.status_code == 429
    body = json.loads(response.body)
    assert body == {
        "code": "QUOTA_EXCEEDED",
        "message": "模型用量已达上限，请升级会员",
        "details": {
            "user_level": "basic",
            "used_tokens": 900_000,
            "pending_reserved_tokens": 100_000,
            "quota_limit": 1_000_000,
            "reset_date": "2026-08-01T00:00:00+00:00",
        },
    }
    proxy_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_runtime_quota_uses_same_billing_snapshot(monkeypatch):
    snapshot = QuotaSnapshot(
        user_level="premium",
        used_tokens=1234,
        pending_reserved_tokens=250,
        quota_limit=10_000_000,
        reset_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    service = SimpleNamespace(get_snapshot=AsyncMock(return_value=snapshot))
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_quota_service",
        AsyncMock(return_value=service),
    )
    identity = _identity(uuid4())

    result = await runtime_run_controller.get_runtime_quota(
        identity=identity,
        db=object(),
    )

    assert result == {
        "user_level": "premium",
        "used_tokens": 1234,
        "pending_reserved_tokens": 250,
        "quota_limit": 10_000_000,
        "reset_date": "2026-08-01T00:00:00+00:00",
    }
    service.get_snapshot.assert_awaited_once_with(user=identity.user)
