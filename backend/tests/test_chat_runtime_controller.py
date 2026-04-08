import io
import os

os.environ.setdefault("DEBUG", "false")

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from modules.chat import runtime_controller as chat_runtime_controller


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
        current_user=SimpleNamespace(id=user_id),
        db=object(),
    )

    assert response.success is True
    assert response.thread_id == expected_thread_id
    assert response.count == 1
    assert response.files == [uploaded_payload]
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
            current_user=SimpleNamespace(id=user_id),
            db=object(),
        )

    assert exc_info.value.status_code == 400
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
        current_user=SimpleNamespace(id=user_id),
        db=object(),
    )

    assert response.success is True
    assert response.thread_id == expected_thread_id
    assert response.message == "Deleted notes.txt"
    delete_thread_upload.assert_awaited_once_with(
        thread_id=expected_thread_id,
        filename="notes.txt",
    )
