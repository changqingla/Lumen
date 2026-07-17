import io
import os
from contextlib import asynccontextmanager

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from middlewares.auth import AuthenticatedIdentity
from modules.chat import runtime_controller as chat_runtime_controller


def _identity(user_id):
    return AuthenticatedIdentity(user=SimpleNamespace(id=user_id), is_guest=False)


@pytest.mark.asyncio
async def test_prepare_session_thread_keeps_runtime_token_server_side(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    chat_service = SimpleNamespace(
        get_session=AsyncMock(
            return_value=SimpleNamespace(
                id=session_id,
                user_id=user_id,
                config={"modelName": "user-model:demo"},
            )
        ),
        update_session_config=AsyncMock(),
    )
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        ensure_thread_exists=AsyncMock(return_value={}),
        resolve_assistant_id=AsyncMock(return_value="trusted-assistant"),
        list_runtime_models=AsyncMock(return_value=[]),
        build_run_request_template=lambda **kwargs: {
            "assistant_id": kwargs["assistant_id"],
            "context": {
                "thread_id": kwargs["thread_id"],
                "model_name": kwargs["model_name"],
            },
            "input": {"messages": []},
        },
    )
    model_service = SimpleNamespace(
        resolve_selected_model=AsyncMock(
            return_value={
                "runtime_model_name": "user-model:demo",
                "dynamic_model_token": "must-not-reach-browser",
            }
        )
    )
    materialization_service = SimpleNamespace(
        sync_session_workspace=AsyncMock(return_value=[]),
        sync_knowledge_documents=AsyncMock(return_value=[]),
    )

    monkeypatch.setattr(chat_runtime_controller, "_create_chat_service", lambda _db: chat_service)
    monkeypatch.setattr(chat_runtime_controller, "_get_insight_runtime_service", lambda: runtime_service)
    monkeypatch.setattr(chat_runtime_controller, "_create_model_config_service", lambda _db: model_service)
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_thread_materialization_service",
        lambda: materialization_service,
    )

    response = await chat_runtime_controller.prepare_session_thread(
        session_id=session_id,
        request=chat_runtime_controller.ThreadPrepareRequest(
            sync_workspace_assets=False,
            sync_kb_documents=False,
        ),
        identity=_identity(user_id),
        db=object(),
    )

    assert response.runs_path == f"/chat-runtime/sessions/{session_id}/runs"
    assert response.run_stream_path == f"/chat-runtime/sessions/{session_id}/runs/stream"
    assert "dynamic_model_token" not in response.run_request_template["context"]
    assert "langgraph_base_url" not in response.model_dump()
    assert "gateway_base_url" not in response.model_dump()


@pytest.mark.asyncio
async def test_prepare_session_thread_persists_managed_knowledge_manifest(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    previous_manifest = [
        {
            "kb_id": str(uuid4()),
            "doc_id": str(uuid4()),
            "content_sha256": "a" * 64,
            "thread_filename": "old.md",
        }
    ]
    next_manifest = {
        "kb_id": str(uuid4()),
        "doc_id": str(uuid4()),
        "document_revision": "2026-07-15T00:00:00+00:00",
        "content_sha256": "b" * 64,
        "thread_filename": "new.md",
        "size_bytes": 42,
        "synced": True,
    }
    chat_service = SimpleNamespace(
        get_session=AsyncMock(
            return_value=SimpleNamespace(
                id=session_id,
                user_id=user_id,
                config={
                    "modelName": "model-1",
                    "kbIds": [],
                    "runtimeKnowledgeFiles": previous_manifest,
                },
            )
        ),
        update_session_config=AsyncMock(),
    )
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        ensure_thread_exists=AsyncMock(return_value={}),
        has_active_thread_run=AsyncMock(return_value=False),
        resolve_assistant_id=AsyncMock(return_value="assistant-1"),
        list_runtime_models=AsyncMock(return_value=[{"name": "model-1"}]),
        build_run_request_template=lambda **kwargs: {
            "assistant_id": kwargs["assistant_id"],
            "context": {"thread_id": kwargs["thread_id"]},
            "input": {"messages": []},
        },
    )
    model_service = SimpleNamespace(
        resolve_selected_model=AsyncMock(
            return_value={"runtime_model_name": "model-1"}
        )
    )

    @asynccontextmanager
    async def _thread_guard(_thread_id):
        yield

    materialization_service = SimpleNamespace(
        thread_guard=_thread_guard,
        sync_session_workspace=AsyncMock(return_value=[]),
        sync_knowledge_documents=AsyncMock(return_value=[next_manifest]),
        cleanup_stale_knowledge_uploads=AsyncMock(),
    )
    scope_service = SimpleNamespace(
        resolve_current_scope=AsyncMock(return_value=object()),
        validate_manifest=MagicMock(),
    )

    monkeypatch.setattr(chat_runtime_controller, "_create_chat_service", lambda _db: chat_service)
    monkeypatch.setattr(chat_runtime_controller, "_get_insight_runtime_service", lambda: runtime_service)
    monkeypatch.setattr(chat_runtime_controller, "_create_model_config_service", lambda _db: model_service)
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_thread_materialization_service",
        lambda: materialization_service,
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_create_runtime_knowledge_scope_service",
        lambda _db: scope_service,
    )

    await chat_runtime_controller.prepare_session_thread(
        session_id=session_id,
        request=chat_runtime_controller.ThreadPrepareRequest(
            sync_workspace_assets=False,
            sync_kb_documents=True,
        ),
        identity=_identity(user_id),
        db=object(),
    )

    assert materialization_service.sync_knowledge_documents.await_args.kwargs[
        "previous_materialized"
    ] == previous_manifest
    config_updates = next(
        call.kwargs["config_updates"]
        for call in chat_service.update_session_config.await_args_list
        if "runtimeKnowledgeFiles" in call.kwargs["config_updates"]
    )
    assert config_updates["runtimeKnowledgeFiles"] == [
        {
            "kb_id": next_manifest["kb_id"],
            "doc_id": next_manifest["doc_id"],
            "document_revision": next_manifest["document_revision"],
            "content_sha256": "b" * 64,
            "thread_filename": "new.md",
            "size_bytes": 42,
        }
    ]
    materialization_service.cleanup_stale_knowledge_uploads.assert_awaited_once_with(
        thread_id=str(session_id),
        desired_filenames=["new.md"],
        guard_acquired=True,
    )


@pytest.mark.asyncio
async def test_prepare_does_not_cleanup_old_files_when_manifest_commit_fails(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()

    async def update_config(*, config_updates, **_kwargs):
        if "runtimeKnowledgeFiles" in config_updates:
            raise RuntimeError("database unavailable")

    chat_service = SimpleNamespace(
        get_session=AsyncMock(
            return_value=SimpleNamespace(
                config={"modelName": "model-1", "kbIds": [], "docIds": []}
            )
        ),
        update_session_config=AsyncMock(side_effect=update_config),
    )
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        ensure_thread_exists=AsyncMock(return_value={}),
        has_active_thread_run=AsyncMock(return_value=False),
        resolve_assistant_id=AsyncMock(return_value="assistant-1"),
        list_runtime_models=AsyncMock(return_value=[{"name": "model-1"}]),
        build_run_request_template=MagicMock(),
    )
    model_service = SimpleNamespace(
        resolve_selected_model=AsyncMock(
            return_value={"runtime_model_name": "model-1"}
        )
    )

    @asynccontextmanager
    async def _thread_guard(_thread_id):
        yield

    materialization_service = SimpleNamespace(
        thread_guard=_thread_guard,
        sync_session_workspace=AsyncMock(),
        sync_knowledge_documents=AsyncMock(return_value=[]),
        cleanup_stale_knowledge_uploads=AsyncMock(),
    )
    scope_service = SimpleNamespace(
        resolve_current_scope=AsyncMock(return_value=object()),
        validate_manifest=MagicMock(),
    )
    monkeypatch.setattr(
        chat_runtime_controller, "_create_chat_service", lambda _db: chat_service
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_insight_runtime_service",
        lambda: runtime_service,
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_create_model_config_service",
        lambda _db: model_service,
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_thread_materialization_service",
        lambda: materialization_service,
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_create_runtime_knowledge_scope_service",
        lambda _db: scope_service,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await chat_runtime_controller.prepare_session_thread(
            session_id=session_id,
            request=chat_runtime_controller.ThreadPrepareRequest(
                sync_workspace_assets=False,
                sync_kb_documents=True,
            ),
            identity=_identity(user_id),
            db=object(),
        )

    materialization_service.cleanup_stale_knowledge_uploads.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_rejects_file_changes_while_thread_run_is_active(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    chat_service = SimpleNamespace(
        get_session=AsyncMock(
            return_value=SimpleNamespace(
                config={"modelName": "model-1", "kbIds": [], "docIds": []}
            )
        ),
        update_session_config=AsyncMock(),
    )
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        ensure_thread_exists=AsyncMock(return_value={}),
        has_active_thread_run=AsyncMock(return_value=True),
        resolve_assistant_id=AsyncMock(return_value="assistant-1"),
        list_runtime_models=AsyncMock(return_value=[{"name": "model-1"}]),
    )
    model_service = SimpleNamespace(
        resolve_selected_model=AsyncMock(
            return_value={"runtime_model_name": "model-1"}
        )
    )

    @asynccontextmanager
    async def _thread_guard(_thread_id):
        yield

    materialization_service = SimpleNamespace(
        thread_guard=_thread_guard,
        sync_session_workspace=AsyncMock(),
        sync_knowledge_documents=AsyncMock(),
        cleanup_stale_knowledge_uploads=AsyncMock(),
    )
    monkeypatch.setattr(
        chat_runtime_controller, "_create_chat_service", lambda _db: chat_service
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_insight_runtime_service",
        lambda: runtime_service,
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_create_model_config_service",
        lambda _db: model_service,
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_thread_materialization_service",
        lambda: materialization_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_runtime_controller.prepare_session_thread(
            session_id=session_id,
            request=chat_runtime_controller.ThreadPrepareRequest(
                sync_workspace_assets=False,
                sync_kb_documents=True,
            ),
            identity=_identity(user_id),
            db=object(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "THREAD_RUN_ACTIVE"
    materialization_service.sync_knowledge_documents.assert_not_awaited()
    chat_service.update_session_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_session_thread_files_proxies_to_runtime_uploads(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    expected_thread_id = str(session_id)
    uploaded_payload = {
        "filename": "notes.txt",
        "size": 11,
        "path": "threads/demo/user-data/uploads/notes.txt",
        "virtual_path": "/mnt/user-data/uploads/notes.txt",
        "artifact_url": f"/api/threads/{expected_thread_id}/artifacts/mnt/user-data/uploads/notes.txt",
    }

    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(
        return_value=SimpleNamespace(id=session_id, user_id=user_id, config={})
    )
    upload_file_object = AsyncMock(return_value=uploaded_payload)
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        upload_file_object=upload_file_object,
    )

    monkeypatch.setattr(chat_runtime_controller, "_create_chat_service", MagicMock(return_value=chat_service))
    monkeypatch.setattr(chat_runtime_controller, "_get_insight_runtime_service", MagicMock(return_value=runtime_service))

    response = await chat_runtime_controller.upload_session_thread_files(
        session_id=session_id,
        files=[UploadFile(filename="notes.txt", file=io.BytesIO(b"hello world"))],
        identity=_identity(user_id),
        db=object(),
    )

    assert response.success is True
    assert response.thread_id == expected_thread_id
    assert response.count == 1
    assert response.files == [
        {
            "filename": "notes.txt",
            "size": 11,
            "virtual_path": "/mnt/user-data/uploads/notes.txt",
        }
    ]
    upload_file_object.assert_awaited_once()
    kwargs = upload_file_object.await_args.kwargs
    assert kwargs["thread_id"] == expected_thread_id
    assert kwargs["filename"] == "notes.txt"
    assert kwargs["content_type"] is None
    kwargs["file_object"].seek(0)
    assert kwargs["file_object"].read() == b"hello world"


@pytest.mark.asyncio
async def test_upload_session_thread_files_rejects_oversized_upload(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()

    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(
        return_value=SimpleNamespace(id=session_id, user_id=user_id, config={})
    )
    upload_file_object = AsyncMock()
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        upload_file_object=upload_file_object,
    )

    monkeypatch.setattr(chat_runtime_controller, "_create_chat_service", MagicMock(return_value=chat_service))
    monkeypatch.setattr(chat_runtime_controller, "_get_insight_runtime_service", MagicMock(return_value=runtime_service))
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_upload_file_size",
        AsyncMock(return_value=chat_runtime_controller.settings.MAX_UPLOAD_SIZE + 1),
    )

    oversized = UploadFile(filename="big.bin", file=io.BytesIO(b"abcdef"))

    with pytest.raises(HTTPException) as exc_info:
        await chat_runtime_controller.upload_session_thread_files(
            session_id=session_id,
            files=[oversized],
            identity=_identity(user_id),
            db=object(),
        )

    assert exc_info.value.status_code == 400
    upload_file_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_session_thread_files_rejects_managed_kb_namespace(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(
        return_value=SimpleNamespace(id=session_id, user_id=user_id, config={})
    )
    upload_file_object = AsyncMock()
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        upload_file_object=upload_file_object,
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_create_chat_service",
        MagicMock(return_value=chat_service),
    )
    monkeypatch.setattr(
        chat_runtime_controller,
        "_get_insight_runtime_service",
        MagicMock(return_value=runtime_service),
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_runtime_controller.upload_session_thread_files(
            session_id=session_id,
            files=[UploadFile(filename="kb__private.md", file=io.BytesIO(b"data"))],
            identity=_identity(user_id),
            db=object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "RESERVED_FILENAME"
    upload_file_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_session_thread_file_proxies_to_runtime_delete(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    expected_thread_id = str(session_id)
    delete_thread_upload = AsyncMock(return_value={"success": True, "message": "Deleted notes.txt"})
    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(
        return_value=SimpleNamespace(id=session_id, user_id=user_id, config={})
    )
    runtime_service = SimpleNamespace(
        build_thread_id=lambda value: value,
        delete_thread_upload=delete_thread_upload,
    )

    monkeypatch.setattr(chat_runtime_controller, "_create_chat_service", MagicMock(return_value=chat_service))
    monkeypatch.setattr(chat_runtime_controller, "_get_insight_runtime_service", MagicMock(return_value=runtime_service))

    response = await chat_runtime_controller.delete_session_thread_file(
        session_id=session_id,
        filename="notes.txt",
        companion_filename="notes.md",
        identity=_identity(user_id),
        db=object(),
    )

    assert response.success is True
    assert response.thread_id == expected_thread_id
    assert response.message == "Deleted notes.txt"
    delete_thread_upload.assert_awaited_once_with(
        thread_id=expected_thread_id,
        filename="notes.txt",
        companion_filename="notes.md",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "companion_filename"),
    [
        ("kb__managed.md", None),
        ("notes.pdf", "kb__managed.md"),
    ],
)
async def test_delete_session_thread_file_rejects_managed_kb_namespace(
    filename,
    companion_filename,
):
    with pytest.raises(HTTPException) as exc_info:
        await chat_runtime_controller.delete_session_thread_file(
            session_id=uuid4(),
            filename=filename,
            companion_filename=companion_filename,
            identity=_identity(uuid4()),
            db=object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "RESERVED_FILENAME"
