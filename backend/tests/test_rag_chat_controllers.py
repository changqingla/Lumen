import os

os.environ["DEBUG"] = "false"

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from middlewares.auth import AuthenticatedIdentity
from modules.chat import controller as chat_controller
from utils import audit_logger


def _identity(user_id):
    return AuthenticatedIdentity(
        user=SimpleNamespace(id=user_id, name="tester", email="tester@example.com"),
        is_guest=False,
    )


class _FakeMessage:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class _FakeWorkspaceAsset:
    def __init__(self, payload):
        self.attachment_id = payload.get("attachment_id")
        self.object_path = payload.get("object_path")
        self._payload = payload

    def to_metadata_payload(self):
        return self._payload


async def _collect_streaming_response(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    if getattr(response, "background", None) is not None:
        await response.background()

    return b"".join(chunks)


@pytest.mark.asyncio
async def test_get_messages_returns_history(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    repo = MagicMock()
    repo.get_session_for_user = AsyncMock(
        return_value=SimpleNamespace(user_id=user_id)
    )
    repo.get_session_messages = AsyncMock(return_value=[_FakeMessage({"id": "m-1", "role": "assistant"})])
    workspace_service = MagicMock()
    workspace_service.load_manifest = AsyncMock(return_value=SimpleNamespace(assets=[]))

    chat_service = MagicMock()
    chat_service.chat_repo = repo
    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(chat_controller, "_create_workspace_service", lambda session_id, user_id: workspace_service)

    response = await chat_controller.get_messages(
        session_id=session_id,
        db=object(),
        identity=_identity(user_id),
    )

    assert response["messages"] == [{"id": "m-1", "role": "assistant"}]


@pytest.mark.asyncio
async def test_get_messages_redacts_workspace_manifest_failure(
    monkeypatch,
    caplog,
):
    marker = "private-workspace-object-detail"
    session_id = uuid4()
    user_id = uuid4()
    repo = MagicMock()
    repo.get_session_for_user = AsyncMock(return_value=SimpleNamespace(user_id=user_id))
    repo.get_session_messages = AsyncMock(
        return_value=[_FakeMessage({"id": "m-1", "role": "assistant"})]
    )
    workspace_service = MagicMock()
    workspace_service.load_manifest = AsyncMock(side_effect=ValueError(marker))
    chat_service = MagicMock()
    chat_service.chat_repo = repo
    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(
        chat_controller,
        "_create_workspace_service",
        lambda session_id, user_id: workspace_service,
    )

    response = await chat_controller.get_messages(
        session_id=session_id,
        db=object(),
        identity=_identity(user_id),
    )

    assert response["messages"] == [{"id": "m-1", "role": "assistant"}]
    assert marker not in caplog.text
    assert "ValueError" in caplog.text


@pytest.mark.asyncio
async def test_get_messages_merges_attachment_status_from_workspace_manifest(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    attachment_id = "att_demo123456"
    object_path = (
        f"v2/tenants/{chat_controller._compute_tenant_key(user_id)}/"
        f"sessions/{session_id}/files/uploads/{attachment_id}/report.docx"
    )
    repo = MagicMock()
    repo.get_session_for_user = AsyncMock(
        return_value=SimpleNamespace(user_id=user_id)
    )
    repo.get_session_messages = AsyncMock(return_value=[
        _FakeMessage({
            "id": "m-1",
            "role": "user",
            "content": "看这个文档",
            "attachments": [{
                "attachment_id": attachment_id,
                "name": "report.docx",
                "object_path": object_path,
                "workspace_path": "input/report.docx",
                "parse_status": "pending",
            }],
        })
    ])

    workspace_service = MagicMock()
    workspace_service.load_manifest = AsyncMock(return_value=SimpleNamespace(assets=[
        _FakeWorkspaceAsset({
            "attachment_id": attachment_id,
            "name": "report.docx",
            "object_path": object_path,
            "workspace_path": "input/report.docx",
            "parse_status": "ready",
            "metadata": {
                "kb_projection": {
                    "kb_id": "kb-default",
                    "doc_id": "doc-1",
                    "status": "ready",
                }
            },
        })
    ]))

    chat_service = MagicMock()
    chat_service.chat_repo = repo
    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(chat_controller, "_create_workspace_service", lambda session_id, user_id: workspace_service)

    response = await chat_controller.get_messages(
        session_id=session_id,
        db=object(),
        identity=_identity(user_id),
    )

    attachments = response["messages"][0]["attachments"]
    assert attachments[0]["parse_status"] == "ready"
    assert attachments[0]["metadata"]["kb_projection"]["status"] == "ready"


@pytest.mark.asyncio
async def test_create_empty_session_returns_created_session(monkeypatch):
    user_id = uuid4()
    session_payload = {
        "id": str(uuid4()),
        "title": "文件会话",
        "lastMessage": "",
        "timestamp": "2026-03-15T00:00:00+00:00",
        "createdAt": "2026-03-15T00:00:00+00:00",
        "updatedAt": "2026-03-15T00:00:00+00:00",
        "messageCount": 0,
        "config": {
            "uiMode": "normal",
            "sourceType": "home",
            "kbIds": [],
            "docIds": [],
            "isKBLocked": False,
        },
    }
    chat_service = MagicMock()
    chat_service.create_empty_session = AsyncMock(
        return_value=SimpleNamespace(to_dict=lambda: session_payload)
    )

    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(chat_controller, "_validate_session_model_name", AsyncMock())

    request = chat_controller.CreateEmptySessionRequest(
        config=chat_controller.ChatSessionConfigPayload(
            uiMode="normal",
            sourceType="home",
            kbIds=[],
            docIds=[],
            isKBLocked=False,
        ),
        title="文件会话",
    )

    response = await chat_controller.create_empty_session(
        request=request,
        db=object(),
        identity=_identity(user_id),
    )

    assert response == session_payload
    chat_service.create_empty_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_empty_session_rejects_invalid_model_name(monkeypatch):
    user_id = uuid4()
    chat_service = MagicMock()
    chat_service.create_empty_session = AsyncMock()

    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(
        chat_controller,
        "_validate_session_model_name",
        AsyncMock(side_effect=HTTPException(status_code=400, detail="所选模型不存在或不可用")),
    )

    request = chat_controller.CreateEmptySessionRequest(
        config=chat_controller.ChatSessionConfigPayload(
            uiMode="normal",
            sourceType="home",
            kbIds=[],
            docIds=[],
            isKBLocked=False,
            modelName="user-model:missing",
        ),
        title="文件会话",
    )

    with pytest.raises(HTTPException, match="所选模型不存在或不可用"):
        await chat_controller.create_empty_session(
            request=request,
            db=object(),
            identity=_identity(user_id),
        )

    chat_service.create_empty_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_message_registers_chat_inline_images_into_workspace_attachments(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    image_payload = base64.b64encode(b"fake-image-bytes").decode("ascii")
    image_data_url = f"data:image/png;base64,{image_payload}"
    runtime_attachment = chat_controller.WorkspaceAttachmentInput(
        attachment_id="runtime-upload:report.pdf",
        name="report.pdf",
        object_path="mnt/user-data/uploads/report.pdf",
        workspace_path="uploads/report.pdf",
        mime_type="application/pdf",
        metadata={
            "runtime_upload": {
                "filename": "report.pdf",
                "virtual_path": "/mnt/user-data/uploads/report.pdf",
            }
        },
    )
    image_asset_payload = {
        "attachment_id": "att_inline_image",
        "session_id": str(session_id),
        "user_id": str(user_id),
        "name": "image-aabbccddeeff.png",
        "object_path": (
            f"v2/tenants/{chat_controller._compute_tenant_key(user_id)}/"
            f"sessions/{session_id}/files/input/image-aabbccddeeff.png"
        ),
        "workspace_path": "input/image-aabbccddeeff.png",
        "mime_type": "image/png",
        "source_kind": "user_upload",
        "role": "source",
        "input_mode": "both",
        "size_bytes": 16,
        "sha256": "aabbccddeeff",
        "view_type": "image",
        "available_views": ["vision"],
        "capabilities": ["vision_read", "image_transform"],
        "parse_status": "ready",
        "created_at": "2026-03-31T00:00:00+00:00",
        "metadata": {"origin": "image_data_url"},
    }

    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        config={"runtime": "lumen"},
    )
    persisted_payload = {"id": "msg-1", "role": "user", "content": "看图"}
    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(return_value=session)
    chat_service.add_message = AsyncMock(return_value=_FakeMessage(persisted_payload))

    workspace_service = MagicMock()
    workspace_service.resolve_request_assets = AsyncMock(
        return_value=[_FakeWorkspaceAsset(image_asset_payload)]
    )

    monkeypatch.setattr(chat_controller, "record_user_prompt_event", AsyncMock())
    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(chat_controller, "_create_workspace_service", lambda session_id, user_id: workspace_service)

    request = chat_controller.AddMessageRequest(
        role="user",
        content="看图",
        image_data_urls=[image_data_url],
        attachments=[runtime_attachment],
    )

    response = await chat_controller.add_message(
        session_id=session_id,
        request=request,
        db=object(),
        identity=_identity(user_id),
    )

    assert response == persisted_payload
    workspace_service.resolve_request_assets.assert_awaited_once_with(
        image_data_urls=[image_data_url],
    )

    persisted_attachments = chat_service.add_message.await_args.args[9]
    assert len(persisted_attachments) == 2
    assert persisted_attachments[0]["name"] == "report.pdf"
    assert persisted_attachments[1]["attachment_id"] == "att_inline_image"
    assert persisted_attachments[1]["metadata"]["origin"] == "image_data_url"
    chat_controller.record_user_prompt_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_message_records_user_question_to_audit_log(monkeypatch, tmp_path):
    session_id = uuid4()
    user_id = uuid4()
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_INCLUDE_PROMPTS", False)
    monkeypatch.setattr(audit_logger, "_last_pruned_on", None)

    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        config={"runtime": "lumen", "modelName": "gpt-5.4"},
    )
    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(return_value=session)
    chat_service.add_message = AsyncMock(
        return_value=_FakeMessage({"id": "msg-1", "role": "user", "content": "帮我总结这篇文章"})
    )

    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)

    response = await chat_controller.add_message(
        session_id=session_id,
        request=chat_controller.AddMessageRequest(role="user", content="帮我总结这篇文章"),
        db=object(),
        identity=_identity(user_id),
    )

    assert response["id"] == "msg-1"
    [log_file] = list(tmp_path.glob("*/user-*.jsonl"))
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["event_type"] == "chat_question"
    assert record["user"]["id"] == str(user_id)
    assert "prompt" not in record
    assert record["prompt_length"] == len("帮我总结这篇文章")
    assert record["metadata"]["session_id"] == str(session_id)
    assert record["metadata"]["message_id"] == "msg-1"
    assert record["metadata"]["model"] == "gpt-5.4"
    assert record["metadata"]["attachment_count"] == 0


@pytest.mark.asyncio
async def test_add_message_does_not_register_inline_images_when_attachment_validation_fails(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    image_payload = base64.b64encode(b"fake-image-bytes").decode("ascii")
    image_data_url = f"data:image/png;base64,{image_payload}"

    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        config={"runtime": "lumen"},
    )
    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(return_value=session)
    chat_service.add_message = AsyncMock()

    workspace_service = MagicMock()
    workspace_service.resolve_request_assets = AsyncMock()

    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(chat_controller, "_create_workspace_service", lambda session_id, user_id: workspace_service)

    request = chat_controller.AddMessageRequest(
        role="user",
        content="看图",
        image_data_urls=[image_data_url],
        attachments=[
            chat_controller.WorkspaceAttachmentInput(
                attachment_id="att_invalid",
                name="bad.pdf",
                object_path="v2/tenants/other/sessions/other/files/input/bad.pdf",
                workspace_path="input/bad.pdf",
            )
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_controller.add_message(
            session_id=session_id,
            request=request,
            db=object(),
            identity=_identity(user_id),
        )

    assert exc_info.value.status_code == 403
    workspace_service.resolve_request_assets.assert_not_called()
    chat_service.add_message.assert_not_called()


@pytest.mark.asyncio
async def test_download_session_artifact_streams_runtime_file(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        config={"runtime": "lumen", "threadId": "thread-1"},
    )
    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(return_value=session)
    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(
        chat_controller,
        "_get_insight_runtime_service",
        lambda: SimpleNamespace(
            gateway_url="http://lumen_gateway:8001",
            request_timeout_seconds=15,
            gateway_request_headers=lambda: {
                "X-Gateway-Internal-Token": "gateway-test-token"
            },
        ),
    )

    class _FakeStreamingHttpxResponse:
        def __init__(self, chunks, headers=None, status_code=200):
            self._chunks = chunks
            self.headers = headers or {}
            self.status_code = status_code
            self.closed = False
            self.request = None

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "upstream error",
                    request=self.request or httpx.Request("GET", "http://example.com"),
                    response=httpx.Response(self.status_code, request=self.request or httpx.Request("GET", "http://example.com")),
                )

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

        async def aclose(self):
            self.closed = True

    fake_clients = []

    class _FakeAsyncClient:
        def __init__(self, timeout, headers, follow_redirects, trust_env):
            self.timeout = timeout
            self.headers = headers
            self.follow_redirects = follow_redirects
            self.trust_env = trust_env
            self.closed = False
            self.response = _FakeStreamingHttpxResponse(
                [b"runtime-", b"artifact"],
                headers={"content-type": "image/png", "content-length": "15"},
            )
            fake_clients.append(self)

        def build_request(self, method, url):
            return httpx.Request(method, url)

        async def send(self, request, stream=False):
            self.response.request = request
            assert stream is True
            return self.response

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(chat_controller.httpx, "AsyncClient", _FakeAsyncClient)

    response = await chat_controller.download_session_artifact(
        session_id=session_id,
        object_path="mnt/user-data/outputs/final-image.png",
        db=object(),
        identity=_identity(user_id),
    )

    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''final-image.png"
    assert response.headers["content-length"] == "15"
    assert fake_clients[0].headers == {
        "X-Gateway-Internal-Token": "gateway-test-token"
    }
    assert fake_clients[0].follow_redirects is False
    assert fake_clients[0].trust_env is False
    assert await _collect_streaming_response(response) == b"runtime-artifact"
    assert fake_clients[0].response.closed is True
    assert fake_clients[0].closed is True


@pytest.mark.asyncio
async def test_download_session_artifact_streams_minio_file(monkeypatch):
    session_id = uuid4()
    user_id = uuid4()
    tenant_key = chat_controller._compute_tenant_key(user_id)
    object_path = f"v2/tenants/{tenant_key}/sessions/{session_id}/files/output/report.txt"
    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        config={},
    )
    chat_service = MagicMock()
    chat_service.get_session = AsyncMock(return_value=session)
    monkeypatch.setattr(chat_controller, "_create_chat_service", lambda db: chat_service)
    monkeypatch.setattr(
        chat_controller,
        "_get_minio_object_exists",
        lambda: AsyncMock(return_value=True),
    )

    import utils.minio_client as minio_client

    monkeypatch.setattr(minio_client, "stream_file", lambda _object_path: iter([b"hello ", b"world"]))

    response = await chat_controller.download_session_artifact(
        session_id=session_id,
        object_path=object_path,
        db=object(),
        identity=_identity(user_id),
    )

    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''report.txt"
    assert await _collect_streaming_response(response) == b"hello world"
