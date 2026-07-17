import asyncio
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest
import httpx
from fastapi import HTTPException

from modules.knowledge import document_task_queue as queue_module
from modules.knowledge.document_task_queue import (
    DocumentTaskLease,
    DocumentTaskQueueWorker,
    RedisDocumentTaskQueue,
)
from modules.knowledge.entities.document import Document
from modules.knowledge.services import document_service as document_service_module
from modules.knowledge.services.document_service import DocumentService
from utils import document_process_service as process_client_module
from utils.document_process_service import (
    DOCUMENT_ERROR_MESSAGES,
    DocumentProcessService,
    DocumentProcessingError,
)
from utils.es_utils import get_user_es_index


class InMemoryQueueRedis:
    """Small Redis model for exercising the queue's Lua state transitions."""

    def __init__(self):
        self.ready = {}
        self.leased = {}
        self.tokens = {}
        self.attempts = {}
        self.cancelled = {}
        self.values = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    async def zscore(self, key, member):
        if key != queue_module.QUEUE_LEASED_KEY:
            raise AssertionError(key)
        return self.leased.get(member)

    async def eval(self, script, key_count, *args):
        argv = args[key_count:]
        if script == queue_module._ENQUEUE_SCRIPT:
            document_id, attempt, ready_at = str(argv[0]), int(argv[1]), float(argv[2])
            if self.cancelled.get(document_id, 0) > ready_at:
                return -1
            self.cancelled.pop(document_id, None)
            if document_id in self.ready or document_id in self.leased:
                return 0
            self.attempts[document_id] = attempt
            self.ready[document_id] = ready_at
            return 1

        if script == queue_module._ACQUIRE_SCRIPT:
            now, lease_until, token = float(argv[0]), float(argv[1]), str(argv[2])
            due = sorted(
                (score, document_id)
                for document_id, score in self.ready.items()
                if score <= now
            )
            for _, document_id in due:
                if self.cancelled.get(document_id, 0) > now:
                    self.ready.pop(document_id, None)
                    self.attempts.pop(document_id, None)
                    continue
                self.cancelled.pop(document_id, None)
                self.ready.pop(document_id)
                self.leased[document_id] = lease_until
                self.tokens[document_id] = token
                return [document_id, str(self.attempts.get(document_id, 1))]
            return []

        if script == queue_module._HEARTBEAT_SCRIPT:
            document_id, token = str(argv[0]), str(argv[1])
            lease_until, now = float(argv[2]), float(argv[3])
            if self.tokens.get(document_id) != token:
                return 0
            if self.cancelled.get(document_id, 0) > now:
                return -1
            self.cancelled.pop(document_id, None)
            if document_id not in self.leased:
                return 0
            self.leased[document_id] = lease_until
            return 1

        if script == queue_module._ACK_SCRIPT:
            document_id, token = str(argv[0]), str(argv[1])
            if self.tokens.get(document_id) != token:
                return 0
            self.leased.pop(document_id, None)
            self.tokens.pop(document_id, None)
            self.attempts.pop(document_id, None)
            self.cancelled.pop(document_id, None)
            return 1

        if script == queue_module._RELEASE_SCRIPT:
            document_id, token, ready_at = str(argv[0]), str(argv[1]), float(argv[2])
            if self.tokens.get(document_id) != token:
                return 0
            self.leased.pop(document_id, None)
            self.tokens.pop(document_id, None)
            if self.cancelled.get(document_id, 0) > ready_at:
                self.attempts.pop(document_id, None)
                return -1
            self.cancelled.pop(document_id, None)
            self.ready[document_id] = ready_at
            return 1

        if script == queue_module._RETRY_SCRIPT:
            document_id, token = str(argv[0]), str(argv[1])
            attempt, ready_at = int(argv[2]), float(argv[3])
            if self.tokens.get(document_id) != token:
                return 0
            self.leased.pop(document_id, None)
            self.tokens.pop(document_id, None)
            if self.cancelled.get(document_id, 0) > ready_at:
                self.attempts.pop(document_id, None)
                return -1
            self.cancelled.pop(document_id, None)
            self.attempts[document_id] = attempt
            self.ready[document_id] = ready_at
            return 1

        if script == queue_module._RECOVER_SCRIPT:
            now, limit = float(argv[0]), int(argv[1])
            self.cancelled = {
                document_id: expires_at
                for document_id, expires_at in self.cancelled.items()
                if expires_at > now
            }
            expired = sorted(
                (score, document_id)
                for document_id, score in self.leased.items()
                if score <= now
            )[:limit]
            for _, document_id in expired:
                if self.cancelled.get(document_id, 0) > now:
                    self.leased[document_id] = self.cancelled[document_id]
                    continue
                self.leased.pop(document_id, None)
                self.tokens.pop(document_id, None)
                self.cancelled.pop(document_id, None)
                self.ready[document_id] = now
            return sum(1 for _, document_id in expired if document_id in self.ready)

        if script == queue_module._CANCEL_SCRIPT:
            document_id = str(argv[0])
            now, cancelled_until = float(argv[1]), float(argv[2])
            self.cancelled[document_id] = cancelled_until
            self.ready.pop(document_id, None)
            lease_until = self.leased.get(document_id)
            if lease_until is None or document_id not in self.tokens:
                self.leased.pop(document_id, None)
                self.tokens.pop(document_id, None)
                self.attempts.pop(document_id, None)
                return 0
            return 1

        if script == queue_module._RELEASE_LOCK_SCRIPT:
            key, token = str(args[0]), str(argv[0])
            if self.values.get(key) == token:
                self.values.pop(key, None)
                return 1
            return 0

        raise AssertionError("Unexpected Lua script")


def _make_worker(
    *, redis_client=None, session_factory=None, processor=None, **overrides
):
    options = {
        "redis_client": redis_client or InMemoryQueueRedis(),
        "session_factory": session_factory or AsyncMock(),
        "concurrency": 1,
        "visibility_timeout_seconds": 30,
        "heartbeat_interval_seconds": 1,
        "max_retries": 2,
        "retry_delay_seconds": 0,
        "reconcile_interval_seconds": 5,
        "reconcile_batch_size": 2,
        "reconcile_max_documents": 3,
        "processor": processor,
    }
    options.update(overrides)
    return DocumentTaskQueueWorker(**options)


@pytest.mark.asyncio
async def test_stale_worker_cannot_acknowledge_recovered_lease():
    redis_client = InMemoryQueueRedis()
    queue = RedisDocumentTaskQueue(redis_client, visibility_timeout_seconds=30)
    document_id = str(uuid4())

    assert await queue.enqueue(document_id) == 1
    first = await queue.acquire()
    assert first is not None
    redis_client.leased[document_id] = time.time() - 1
    assert await queue.recover_expired() == 1
    second = await queue.acquire()
    assert second is not None
    assert second.token != first.token

    assert await queue.acknowledge(first) is False
    assert redis_client.tokens[document_id] == second.token
    assert await queue.acknowledge(second) is True


@pytest.mark.asyncio
async def test_cancel_waits_until_running_worker_observes_cancellation():
    redis_client = InMemoryQueueRedis()
    queue = RedisDocumentTaskQueue(redis_client, visibility_timeout_seconds=30)
    document_id = str(uuid4())
    await queue.enqueue(document_id)
    lease = await queue.acquire()
    assert lease is not None

    cancel_task = asyncio.create_task(queue.cancel(document_id, wait_seconds=1))
    await asyncio.sleep(0.01)
    assert await queue.heartbeat(lease) == -1
    assert cancel_task.done() is False
    assert document_id in redis_client.leased
    assert await queue.acknowledge(lease) is True
    assert await cancel_task is True
    assert document_id not in redis_client.leased


@pytest.mark.asyncio
async def test_cancel_does_not_settle_before_processor_cancellation_cleanup_finishes():
    redis_client = InMemoryQueueRedis()
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def processor(_document_id):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await allow_cleanup.wait()

    worker = _make_worker(redis_client=redis_client, processor=processor)
    worker._running = True
    worker.heartbeat_interval_seconds = 0.01
    document_id = str(uuid4())
    await worker.queue.enqueue(document_id)
    lease = await worker.queue.acquire()
    assert lease is not None
    handle_task = asyncio.create_task(worker._handle_lease(lease, worker_index=0))
    await started.wait()

    cancel_task = asyncio.create_task(worker.queue.cancel(document_id, wait_seconds=1))
    await cleanup_started.wait()
    await asyncio.sleep(0.02)

    assert cancel_task.done() is False
    assert document_id in redis_client.leased
    allow_cleanup.set()
    await handle_task
    assert await cancel_task is True
    assert document_id not in redis_client.leased
    assert document_id not in redis_client.cancelled


@pytest.mark.asyncio
async def test_maintenance_does_not_recover_cancelled_lease_before_worker_ack():
    redis_client = InMemoryQueueRedis()
    queue = RedisDocumentTaskQueue(redis_client, visibility_timeout_seconds=30)
    document_id = str(uuid4())
    await queue.enqueue(document_id)
    lease = await queue.acquire()
    assert lease is not None
    cancelled_until = time.time() + 300
    redis_client.cancelled[document_id] = cancelled_until
    redis_client.leased[document_id] = time.time() - 1

    assert await queue.recover_expired() == 0
    assert redis_client.leased[document_id] == cancelled_until
    assert redis_client.tokens[document_id] == lease.token
    assert await queue.acknowledge(lease) is True


@pytest.mark.asyncio
async def test_worker_schedules_bounded_retry_and_restores_queued_db_state():
    processor = AsyncMock(return_value=Document.STATUS_FAILED)
    worker = _make_worker(processor=processor)
    worker._running = True
    worker._update_document_state = AsyncMock()
    lease = DocumentTaskLease(str(uuid4()), attempt=1, token="lease-token")
    worker.queue.heartbeat = AsyncMock(return_value=1)
    worker.queue.schedule_retry = AsyncMock(return_value=1)

    await worker._handle_lease(lease, worker_index=0)

    worker.queue.schedule_retry.assert_awaited_once_with(lease, delay_seconds=0.0)
    worker._update_document_state.assert_awaited_once_with(
        lease.document_id,
        status=Document.STATUS_QUEUED,
        error_message=None,
    )


class _PageResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _PageSession:
    def __init__(self, factory):
        self.factory = factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, _statement):
        return _PageResult(self.factory.pages.pop(0))


class _PageSessionFactory:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return _PageSession(self)


@pytest.mark.asyncio
async def test_reconciliation_is_lock_protected_paginated_and_bounded():
    ids = sorted([uuid4() for _ in range(5)])
    session_factory = _PageSessionFactory(
        [ids[:2], ids[2:3], ids[3:], []],
    )
    redis_client = InMemoryQueueRedis()
    worker = _make_worker(redis_client=redis_client, session_factory=session_factory)
    worker.queue.enqueue = AsyncMock(return_value=1)

    assert await worker.reconcile_queued_documents() == 3
    assert redis_client.values[queue_module.QUEUE_RECONCILE_CURSOR_KEY] == str(ids[2])
    assert await worker.reconcile_queued_documents() == 2
    assert session_factory.calls == 4
    assert [item.args[0] for item in worker.queue.enqueue.await_args_list] == [
        str(document_id) for document_id in ids
    ]
    assert queue_module.QUEUE_RECONCILE_CURSOR_KEY not in redis_client.values
    assert queue_module.QUEUE_RECONCILE_LOCK_KEY not in redis_client.values


@pytest.mark.asyncio
async def test_worker_start_does_not_contact_redis_synchronously():
    class ExplodingRedis:
        def __getattr__(self, _name):
            raise AssertionError("Redis must not be touched during start")

    worker = _make_worker(redis_client=ExplodingRedis(), processor=AsyncMock())
    await worker.start()
    await worker.stop()


@pytest.mark.asyncio
async def test_enqueue_failure_leaves_document_in_recoverable_queued_state(monkeypatch):
    document_id = str(uuid4())
    document = SimpleNamespace(
        id=document_id,
        name="report.pdf",
        size=10,
        status=Document.STATUS_UPLOADING,
        file_path="reader-uploads/kb/owner/report.pdf",
    )
    service = DocumentService(db=object())
    monkeypatch.setattr(
        service, "_verify_kb_write_access", AsyncMock(return_value=object())
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=document)
    service.doc_repo.update_status = AsyncMock()
    monkeypatch.setattr(
        document_service_module,
        "get_object_metadata",
        AsyncMock(return_value=SimpleNamespace(size=10)),
    )
    monkeypatch.setattr(
        document_service_module,
        "enqueue_document_task",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    )

    result = await service.complete_direct_upload("kb-id", "user-id", document_id)

    assert result["status"] == Document.STATUS_PROCESSING
    service.doc_repo.update_status.assert_awaited_once_with(
        document,
        Document.STATUS_QUEUED,
        error_message=None,
    )


@pytest.mark.asyncio
async def test_deleting_active_document_cancels_lease_before_storage_cleanup(
    monkeypatch,
):
    document_id = str(uuid4())
    kb_id = str(uuid4())
    owner_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        kb_id=kb_id,
        name="report.pdf",
        status=Document.STATUS_PROCESSING,
        file_path=None,
        markdown_path=None,
    )
    db = SimpleNamespace(refresh=AsyncMock())
    service = DocumentService(db=db)
    monkeypatch.setattr(
        service,
        "_verify_kb_write_access",
        AsyncMock(return_value=SimpleNamespace(owner_id=owner_id)),
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=document)
    service.doc_repo.delete = AsyncMock()
    service.kb_repo.sync_contents_count = AsyncMock()
    cancel = AsyncMock(return_value=True)
    delete_from_es = AsyncMock()
    monkeypatch.setattr(document_service_module, "cancel_document_task", cancel)
    monkeypatch.setattr(
        document_service_module.DocumentProcessService,
        "delete_document_from_es",
        delete_from_es,
    )

    await service.delete_document(document_id, kb_id, str(owner_id))

    cancel.assert_awaited_once_with(document_id)
    db.refresh.assert_awaited_once_with(document)
    service.doc_repo.delete.assert_awaited_once_with(document)
    delete_from_es.assert_awaited_once()


@pytest.mark.asyncio
async def test_deletion_keeps_document_when_rag_cancellation_is_still_pending(
    monkeypatch,
):
    document_id = str(uuid4())
    kb_id = str(uuid4())
    owner_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        kb_id=kb_id,
        name="report.md",
        status=Document.STATUS_EMBEDDING,
        parse_task_id="active-rag-task",
        file_path=None,
        markdown_path=None,
    )
    db = SimpleNamespace(refresh=AsyncMock())
    service = DocumentService(db=db)
    monkeypatch.setattr(
        service,
        "_verify_kb_write_access",
        AsyncMock(return_value=SimpleNamespace(owner_id=owner_id)),
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=document)
    service.doc_repo.delete = AsyncMock()
    service._settle_rag_task_for_deletion = AsyncMock(return_value=False)
    monkeypatch.setattr(
        document_service_module,
        "cancel_document_task",
        AsyncMock(return_value=True),
    )
    delete_from_es = AsyncMock()
    monkeypatch.setattr(
        document_service_module.DocumentProcessService,
        "delete_document_from_es",
        delete_from_es,
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.delete_document(document_id, kb_id, str(owner_id))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RAG_CANCELLATION_PENDING"
    service.doc_repo.delete.assert_not_awaited()
    delete_from_es.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_cancellation_waits_for_terminal_status(monkeypatch):
    service = DocumentService(db=object())
    get_status = AsyncMock(
        side_effect=[
            {"status": "embedding"},
            {"status": "cancelled"},
        ]
    )
    cancel = AsyncMock(
        return_value={
            "state": "cancellation_requested",
            "task": {"status": "embedding"},
        }
    )
    monkeypatch.setattr(
        document_service_module.DocumentProcessService,
        "get_task_status",
        get_status,
    )
    monkeypatch.setattr(
        document_service_module.DocumentProcessService,
        "cancel_task",
        cancel,
    )
    monkeypatch.setattr(document_service_module.asyncio, "sleep", AsyncMock())

    assert await service._settle_rag_task_for_deletion("rag-task") is True
    cancel.assert_awaited_once_with("rag-task")
    assert get_status.await_count == 2


@pytest.mark.asyncio
async def test_document_process_client_calls_rag_cancel_endpoint(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "data": {
                    "state": "cancellation_requested",
                    "task": {"status": "embedding"},
                },
            }

    client = SimpleNamespace(delete=AsyncMock(return_value=_Response()))
    monkeypatch.setattr(
        process_client_module, "get_internal_http_client", lambda: client
    )

    result = await DocumentProcessService.cancel_task("rag-task")

    assert result["state"] == "cancellation_requested"
    client.delete.assert_awaited_once()
    assert client.delete.await_args.args[0].endswith("/task/rag-task")


def test_rag_parse_idempotency_key_matches_protocol_vector():
    content_sha256 = hashlib.sha256(b"markdown").hexdigest()

    assert (
        process_client_module._build_parse_idempotency_key(
            "11111111-1111-1111-1111-111111111111",
            content_sha256,
        )
        == "863f4aa3b6996f51323de50d26f698159faa637417be1e0a16e08606e4d2dc28"
    )


@pytest.mark.asyncio
async def test_document_process_client_streams_file_and_sends_idempotency_key(
    monkeypatch,
    tmp_path,
):
    document_id = "11111111-1111-1111-1111-111111111111"
    markdown_path = tmp_path / "paper.md"
    markdown_path.write_bytes(b"markdown")
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "data": {"task_id": "rag-task"}}

    class _Client:
        async def post(self, url, *, files, data, headers):
            upload = files["file"]
            captured["url"] = url
            captured["data"] = dict(data)
            captured["headers"] = dict(headers)
            captured["file"] = upload[1]
            captured["content"] = upload[1].read()
            assert upload[0] == "paper.md"
            assert upload[2] == "text/markdown"
            assert not isinstance(upload[1], bytes)
            assert not upload[1].closed
            return _Response()

    monkeypatch.setattr(process_client_module, "get_internal_http_client", _Client)
    monkeypatch.setattr(
        process_client_module,
        "get_rag_internal_headers",
        lambda: {"X-RAG-Internal-Token": "test-token"},
    )

    result = await DocumentProcessService.parse_document(
        str(markdown_path),
        document_id,
        "reader_test",
        "paper.md",
    )

    assert result == {"task_id": "rag-task"}
    assert captured["content"] == b"markdown"
    assert captured["file"].closed
    assert captured["headers"] == {"X-RAG-Internal-Token": "test-token"}
    assert captured["data"]["document_id"] == document_id
    assert captured["data"]["idempotency_key"] == (
        "863f4aa3b6996f51323de50d26f698159faa637417be1e0a16e08606e4d2dc28"
    )


@pytest.mark.asyncio
async def test_document_process_client_redacts_remote_failure_boundary(
    monkeypatch,
    tmp_path,
    caplog,
):
    marker = "SECRET_RESPONSE_BODY_token=private-value"
    markdown_path = tmp_path / f"{marker}.md"
    markdown_path.write_bytes(b"markdown")
    request = httpx.Request(
        "POST",
        f"https://rag.internal/parse?token={marker}",
    )
    response = httpx.Response(502, request=request, text=marker)

    class _Client:
        async def post(self, *_args, **_kwargs):
            raise httpx.HTTPStatusError(
                f"remote failure {marker}",
                request=request,
                response=response,
            )

    monkeypatch.setattr(process_client_module, "get_internal_http_client", _Client)
    monkeypatch.setattr(process_client_module.settings, "EMBEDDING_API_KEY", marker)
    monkeypatch.setattr(
        process_client_module.settings,
        "EMBEDDING_BASE_URL",
        f"https://embedding.internal/?token={marker}",
    )
    caplog.set_level(logging.ERROR)

    with pytest.raises(DocumentProcessingError) as exc_info:
        await DocumentProcessService.parse_document(
            str(markdown_path),
            "11111111-1111-1111-1111-111111111111",
            "reader_test",
            f"{marker}.md",
        )

    assert exc_info.value.stage == "index_submit"
    assert exc_info.value.public_message == DOCUMENT_ERROR_MESSAGES["index_submit"]
    assert marker not in caplog.text
    assert "https://" not in caplog.text
    assert str(markdown_path) not in caplog.text


@pytest.mark.asyncio
async def test_document_process_client_discards_remote_messages_and_extra_data(
    monkeypatch,
):
    marker = "SECRET_REMOTE_MESSAGE_token=private-value"

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "status": "failed",
                "message": marker,
                "data": {
                    "total_chunks": "7",
                    "errors": [marker],
                    "source_path": f"/private/{marker}",
                },
            }

    client = SimpleNamespace(get=AsyncMock(return_value=_Response()))
    monkeypatch.setattr(
        process_client_module,
        "get_internal_http_client",
        lambda: client,
    )

    result = await DocumentProcessService.get_task_status("rag-task")

    assert result == {"status": "failed", "data": {"total_chunks": 7}}
    assert marker not in repr(result)


@pytest.mark.asyncio
async def test_document_process_client_sanitizes_cancellation_payload(monkeypatch):
    marker = "SECRET_CANCEL_MESSAGE_token=private-value"

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": True,
                "message": marker,
                "data": {
                    "state": "cancellation_requested",
                    "task": {"status": "embedding", "message": marker},
                    "debug": marker,
                },
            }

    client = SimpleNamespace(delete=AsyncMock(return_value=_Response()))
    monkeypatch.setattr(
        process_client_module,
        "get_internal_http_client",
        lambda: client,
    )

    result = await DocumentProcessService.cancel_task("rag-task")

    assert result == {
        "state": "cancellation_requested",
        "task": {"status": "embedding"},
    }
    assert marker not in repr(result)


def test_internal_queued_status_is_exposed_as_processing():
    assert Document.public_status(Document.STATUS_QUEUED) == Document.STATUS_PROCESSING


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_processor_resolves_owner_index_and_object_path_from_database(
    monkeypatch,
    tmp_path,
):
    document_id = str(uuid4())
    owner_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        kb_id=uuid4(),
        name="report.pdf",
        status=Document.STATUS_QUEUED,
        parse_task_id=None,
        mineru_task_id=None,
        markdown_path=None,
        file_path="reader-uploads/kb/owner/report.pdf",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(document)))
    service = DocumentService(db=db)
    service.kb_repo.get_by_id_any = AsyncMock(
        return_value=SimpleNamespace(owner_id=owner_id),
    )
    service.doc_repo.update_status = AsyncMock()
    service._delete_existing_document_index = AsyncMock()
    service._process_document_pipeline = AsyncMock()
    service._reload_document_status = AsyncMock(return_value=Document.STATUS_READY)
    downloaded_path = tmp_path / "downloaded-report.pdf"
    download_args = {}

    @asynccontextmanager
    async def fake_temporary_download(object_name, **kwargs):
        download_args.update(object_name=object_name, **kwargs)
        downloaded_path.write_bytes(b"pdf-content")
        try:
            yield downloaded_path
        finally:
            downloaded_path.unlink(missing_ok=True)

    monkeypatch.setattr(
        document_service_module, "temporary_download", fake_temporary_download
    )

    assert await service.process_queued_document(document_id) == Document.STATUS_READY

    owner_index = get_user_es_index(str(owner_id))
    service._delete_existing_document_index.assert_awaited_once_with(
        document_id,
        owner_index,
    )
    service._process_document_pipeline.assert_awaited_once_with(
        document_id,
        owner_index,
        downloaded_path,
        "report.pdf",
    )
    assert download_args["object_name"] == "kb/owner/report.pdf"
    assert download_args["suffix"] == ".pdf"
    assert (
        download_args["max_bytes"] == document_service_module.settings.MAX_UPLOAD_SIZE
    )
    assert not downloaded_path.exists()


@pytest.mark.asyncio
async def test_processor_resumes_existing_rag_task_instead_of_resubmitting(monkeypatch):
    document_id = str(uuid4())
    document = SimpleNamespace(
        id=document_id,
        kb_id=uuid4(),
        name="report.md",
        status=Document.STATUS_EMBEDDING,
        parse_task_id="existing-rag-task",
        mineru_task_id=None,
        markdown_path="reader-uploads/kb/owner/markdown/report.md",
        file_path="reader-uploads/kb/owner/report.md",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(document)))
    service = DocumentService(db=db)
    service.kb_repo.get_by_id_any = AsyncMock(
        return_value=SimpleNamespace(owner_id=uuid4())
    )
    service.kb_repo.sync_contents_count = AsyncMock()
    service._poll_parse_task = AsyncMock(return_value=True)
    service._reload_document_status = AsyncMock(return_value=Document.STATUS_READY)
    parse_document = AsyncMock()
    monkeypatch.setattr(
        document_service_module.DocumentProcessService,
        "parse_document",
        parse_document,
    )

    assert await service.process_queued_document(document_id) == Document.STATUS_READY

    service._poll_parse_task.assert_awaited_once_with(
        document,
        "existing-rag-task",
        service.doc_repo,
    )
    parse_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_processor_rejects_tampered_persisted_markdown_and_rebuilds_from_source(
    monkeypatch,
    tmp_path,
):
    document_id = str(uuid4())
    owner_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        kb_id=uuid4(),
        name="report.md",
        status=Document.STATUS_QUEUED,
        parse_task_id=None,
        mineru_task_id=None,
        markdown_path="reader-uploads/kb/owner/markdown/report.md",
        markdown_sha256=hashlib.sha256(b"expected").hexdigest(),
        materialization_revision=3,
        file_path="reader-uploads/kb/owner/report.md",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(document)))
    service = DocumentService(db=db)
    service.kb_repo.get_by_id_any = AsyncMock(
        return_value=SimpleNamespace(owner_id=owner_id),
    )
    service.doc_repo.update_status = AsyncMock()
    service.doc_repo.update_markdown_path = AsyncMock()
    service._delete_existing_document_index = AsyncMock()
    service._retry_chunking_only = AsyncMock()
    service._process_document_pipeline = AsyncMock()
    service._reload_document_status = AsyncMock(return_value=Document.STATUS_READY)
    downloaded_objects = []

    @asynccontextmanager
    async def fake_temporary_download(object_name, **_kwargs):
        downloaded_objects.append(object_name)
        temp_path = tmp_path / f"download-{len(downloaded_objects)}.md"
        content = b"tampered" if "markdown" in object_name else b"source"
        temp_path.write_bytes(content)
        try:
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)

    monkeypatch.setattr(
        document_service_module,
        "temporary_download",
        fake_temporary_download,
    )

    assert await service.process_queued_document(document_id) == Document.STATUS_READY

    service._retry_chunking_only.assert_not_awaited()
    service.doc_repo.update_markdown_path.assert_not_awaited()
    service._process_document_pipeline.assert_awaited_once()
    assert downloaded_objects == [
        "kb/owner/markdown/report.md",
        "kb/owner/report.md",
    ]


@pytest.mark.asyncio
async def test_parse_poller_never_persists_or_logs_remote_failure_message(
    monkeypatch,
    caplog,
):
    marker = "SECRET_RAG_FAILURE token=private-value /private/source.md"
    document = SimpleNamespace(id=str(uuid4()))
    repository = SimpleNamespace(update_status=AsyncMock())
    service = DocumentService(db=object())
    monkeypatch.setattr(document_service_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        document_service_module.DocumentProcessService,
        "get_task_status",
        AsyncMock(
            return_value={
                "status": "failed",
                "message": marker,
                "data": {"errors": [marker]},
            }
        ),
    )
    caplog.set_level(logging.WARNING)

    assert await service._poll_parse_task(document, "rag-task", repository) is False

    repository.update_status.assert_awaited_once_with(
        document,
        Document.STATUS_FAILED,
        error_message=DOCUMENT_ERROR_MESSAGES["index_failed"],
    )
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_mineru_poller_never_uses_remote_failure_message(monkeypatch, caplog):
    marker = "SECRET_MINERU_FAILURE token=private-value https://private.invalid/query"
    service = DocumentService(db=object())
    monkeypatch.setattr(document_service_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        document_service_module.MineruService,
        "get_task_status",
        AsyncMock(return_value={"status": "failed", "message": marker}),
    )
    caplog.set_level(logging.WARNING)

    with pytest.raises(DocumentProcessingError) as exc_info:
        await service._poll_mineru_task("mineru-task", str(uuid4()), max_attempts=1)

    assert exc_info.value.public_message == DOCUMENT_ERROR_MESSAGES["conversion"]
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_document_worker_persists_stable_error_without_exception_text(caplog):
    marker = "SECRET_WORKER_FAILURE token=private-value /private/file.md"
    worker = _make_worker(
        processor=AsyncMock(side_effect=RuntimeError(marker)),
        max_retries=0,
    )
    worker._running = True
    worker.queue.heartbeat = AsyncMock(return_value=1)
    worker.queue.acknowledge = AsyncMock(return_value=True)
    worker._update_document_state = AsyncMock()
    lease = DocumentTaskLease(str(uuid4()), attempt=1, token="lease-token")
    caplog.set_level(logging.ERROR)

    await worker._handle_lease(lease, worker_index=0)

    worker._update_document_state.assert_awaited_once_with(
        lease.document_id,
        status=Document.STATUS_FAILED,
        error_message=DOCUMENT_ERROR_MESSAGES["processing"],
    )
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_document_status_redacts_historical_database_error_message():
    marker = "SECRET_DATABASE_ERROR token=private-value https://private.invalid/query"
    document = SimpleNamespace(
        status=Document.STATUS_FAILED,
        error_message=marker,
        chunk_count=0,
    )
    service = DocumentService(db=object())
    service._verify_kb_access = AsyncMock(return_value=object())
    service.doc_repo.get_by_id = AsyncMock(return_value=document)

    result = await service.get_document_status("doc-id", "kb-id", "user-id")

    assert result == {
        "status": Document.STATUS_FAILED,
        "errorMessage": DOCUMENT_ERROR_MESSAGES["processing"],
        "chunkCount": 0,
    }
    assert marker not in repr(result)


@pytest.mark.asyncio
async def test_markdown_download_error_is_stable_and_logs_no_exception_text(
    monkeypatch,
    caplog,
):
    marker = "SECRET_STORAGE_ERROR token=private-value /private/object.md"
    document = SimpleNamespace(markdown_path=f"reader-uploads/{marker}")
    service = DocumentService(db=object())
    service._verify_kb_access = AsyncMock(return_value=object())
    service.doc_repo.get_by_id = AsyncMock(return_value=document)
    monkeypatch.setattr(
        "utils.minio_client.download_file",
        AsyncMock(side_effect=RuntimeError(marker)),
    )
    caplog.set_level(logging.ERROR)

    with pytest.raises(HTTPException) as exc_info:
        await service.get_document_markdown("doc-id", "kb-id", "user-id")

    assert exc_info.value.detail["error"]["message"] == (
        "Failed to retrieve markdown content"
    )
    assert marker not in caplog.text
    assert marker not in repr(exc_info.value.detail)
