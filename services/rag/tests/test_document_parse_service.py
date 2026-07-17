from __future__ import annotations

import asyncio
import json
import logging
import sys
import types
from concurrent.futures import Executor, Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# The control-flow tests in this module do not instantiate ML or ES clients.
# Stub those optional, heavyweight imports before loading the service module.
embedding_package = types.ModuleType("embedding")
embedding_package.__path__ = []
chunk_embedder_module = types.ModuleType("embedding.chunk_embedder")


class _EmbeddingConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _ChunkEmbedder:
    def __init__(self, config):
        self.config = config


chunk_embedder_module.EmbeddingConfig = _EmbeddingConfig
chunk_embedder_module.ChunkEmbedder = _ChunkEmbedder
sys.modules.setdefault("embedding", embedding_package)
sys.modules.setdefault("embedding.chunk_embedder", chunk_embedder_module)

common_utils_module = types.ModuleType("common_utils")
common_utils_module.DeepRAGCommonUtils = type("DeepRAGCommonUtils", (), {})
sys.modules.setdefault("common_utils", common_utils_module)

import document_parse_service as service_module  # noqa: E402
from document_parse_service import (  # noqa: E402
    DocumentParseService,
    DocumentParseTask,
    LeaseLostError,
)
from redis_task_queue import QueuePriority, TaskLease  # noqa: E402
from task_metadata import sanitize_task_metadata  # noqa: E402


class _InlineExecutor(Executor):
    """Deterministic executor for control-flow tests that must not leak threads."""

    def submit(self, function, /, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future


def _service_with_settings(**overrides):
    defaults = {
        "EMBEDDING_API_KEY": "",
        "EMBEDDING_BASE_URL": "",
        "CV_API_KEY": "",
        "CV_BASE_URL": "",
        "ES_HOST": "",
        "ES_USERNAME": "",
        "ES_PASSWORD": "",
        "TEMP_DIR": "/tmp/deeprag-parse-tests",
    }
    defaults.update(overrides)
    service = DocumentParseService.__new__(DocumentParseService)
    service._settings = SimpleNamespace(**defaults)
    service._blocking_executor = _InlineExecutor()
    service._payload_dir = Path("/tmp")
    return service


@pytest.mark.asyncio
async def test_initialize_recovers_stale_leases_before_starting_workers():
    events = []

    async def initialize_queue():
        events.append("queue")
        return True

    async def recover(*, force):
        assert force is True
        events.append("recovery")

    async def start_workers():
        events.append("workers")

    service = _service_with_settings()
    service._task_lock = asyncio.Lock()
    service._workers_started = False
    service.task_queue = SimpleNamespace(initialize=initialize_queue)
    service._recover_stale_tasks_if_due = recover
    service.start_workers = start_workers

    await service.initialize()

    assert events == ["queue", "recovery", "workers"]
    assert service._workers_started is True


@pytest.mark.asyncio
async def test_async_chunking_passes_ir_table_config_to_worker(tmp_path, monkeypatch):
    source = tmp_path / "table.xlsx"
    source.write_bytes(b"test workbook")
    ir_table_config = {
        "auto_unmerge": False,
        "keep_title": True,
        "only_columns": ["A", "C"],
    }
    task = DocumentParseTask(
        task_id="ir-table-unit-test",
        filename=source.name,
        file_size=source.stat().st_size,
        chunk_config={"parser_type": "ir-table", "ir_table_config": ir_table_config},
        embedding_config={},
        store_config={},
    )
    task.source_path = str(source)
    captured = {}

    def fake_chunk_worker(*args):
        captured["ir_table_config"] = args[-1]
        return {"success": True, "chunks": [], "full_content": ""}

    monkeypatch.setattr(service_module, "process_chunk_in_process", fake_chunk_worker)

    result = await _service_with_settings()._process_chunking(task)

    assert result["success"] is True
    assert captured["ir_table_config"] == ir_table_config


@pytest.mark.asyncio
async def test_chunking_uses_distinct_opaque_workspace_for_each_lease(
    tmp_path,
    monkeypatch,
):
    service = _service_with_settings(TEMP_DIR=str(tmp_path / "work"))
    service._payload_dir = tmp_path / "payloads"
    service._payload_dir.mkdir()
    source = service._payload_path_for("../../private-task", "paper.pdf")
    source.write_bytes(b"document")
    task = DocumentParseTask(
        task_id="../../private-task",
        filename="paper.pdf",
        file_size=source.stat().st_size,
        chunk_config={},
        embedding_config={},
        store_config={},
    )
    task.source_path = str(source)
    worker_paths = []

    def fake_chunk_worker(file_path, *_args):
        worker_paths.append(Path(file_path))
        return {"success": True, "chunks": [], "full_content": ""}

    monkeypatch.setattr(service_module, "process_chunk_in_process", fake_chunk_worker)

    first = await service._process_chunking(task, lease_token="private-lease-a")
    second = await service._process_chunking(task, lease_token="private-lease-b")

    assert first["success"] is True
    assert second["success"] is True
    assert worker_paths[0].parent != worker_paths[1].parent
    assert "private-task" not in str(worker_paths)
    assert "private-lease" not in str(worker_paths)
    assert source.is_file()


def test_payload_cleanup_refuses_paths_outside_managed_root(tmp_path):
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_payload = external_dir / "payload.pdf"
    external_payload.write_bytes(b"must survive")
    task = DocumentParseTask(
        task_id="task",
        filename=external_payload.name,
        file_size=external_payload.stat().st_size,
        chunk_config={},
        embedding_config={},
        store_config={},
    )
    task.source_path = str(external_payload)
    service = _service_with_settings()
    service._payload_dir = managed_root

    service._cleanup_task_payload(task)

    assert external_payload.read_bytes() == b"must survive"
    assert task.source_path is None


def test_payload_validation_rejects_symlink_escape(tmp_path):
    managed_root = tmp_path / "managed"
    task_dir = managed_root / "task"
    task_dir.mkdir(parents=True)
    external_payload = tmp_path / "external.pdf"
    external_payload.write_bytes(b"private")
    escaped_link = task_dir / "payload.pdf"
    escaped_link.symlink_to(external_payload)
    service = _service_with_settings()
    service._payload_dir = managed_root

    assert service._validated_payload_path(escaped_link) is None


def test_persisted_task_is_secret_free_and_worker_uses_current_settings():
    task = DocumentParseTask(
        task_id="legacy-task",
        filename="paper.pdf",
        file_size=10,
        chunk_config={
            "cv_model_config": {
                "model_factory": "OpenAI",
                "api_key": "legacy-cv-key",
                "base_url": "https://legacy-cv.test/v1",
            }
        },
        embedding_config={
            "model_factory": "OpenAI",
            "model_name": "embed",
            "api_key": "legacy-embedding-key",
            "base_url": "https://legacy-embedding.test/v1",
        },
        store_config={
            "es_host": "https://legacy-es.test:9200",
            "index_name": "documents",
            "password": "legacy-es-password",
        },
    )
    service = _service_with_settings(
        EMBEDDING_API_KEY="current-embedding-key",
        EMBEDDING_BASE_URL="https://current-embedding.test/v1",
        CV_API_KEY="current-cv-key",
        CV_BASE_URL="https://current-cv.test/v1",
        ES_HOST="https://current-es.test:9200",
        ES_USERNAME="elastic",
        ES_PASSWORD="current-es-password",
    )

    persisted_json = json.dumps(task.to_persisted_dict())
    assert "legacy-embedding-key" not in persisted_json
    assert "legacy-cv-key" not in persisted_json
    assert "legacy-es-password" not in persisted_json

    assert service._resolve_embedding_config(task)["api_key"] == "current-embedding-key"
    assert service._resolve_cv_config(task)["api_key"] == "current-cv-key"
    assert service._resolve_store_config(task)["password"] == "current-es-password"


def test_legacy_task_secrets_are_a_fallback_when_worker_config_is_empty():
    task = DocumentParseTask(
        task_id="legacy-task",
        filename="paper.pdf",
        file_size=10,
        chunk_config={"cv_model_config": {"api_key": "legacy-cv-key"}},
        embedding_config={"api_key": "legacy-embedding-key"},
        store_config={"password": "legacy-es-password"},
    )
    service = _service_with_settings()

    assert service._resolve_embedding_config(task)["api_key"] == "legacy-embedding-key"
    assert service._resolve_cv_config(task)["api_key"] == "legacy-cv-key"
    assert service._resolve_store_config(task)["password"] == "legacy-es-password"


@pytest.mark.asyncio
async def test_legacy_redis_task_is_restored_then_rewritten_without_secrets():
    legacy_data = {
        "task_id": "legacy-queued-task",
        "priority": 3,
        "retry_count": 1,
        "filename": "paper.pdf",
        "file_size": 10,
        "status": "queued",
        "progress": 0.0,
        "message": "queued",
        "created_at": "2026-01-01T00:00:00",
        "chunk_config": {"cv_model_config": {"api_key": "legacy-cv-key"}},
        "embedding_config": {"api_key": "legacy-embedding-key"},
        "store_config": {"password": "legacy-es-password"},
    }

    class LegacyQueue:
        def __init__(self):
            self.data = dict(legacy_data)

        async def get_task_data(self, _task_id):
            return dict(self.data)

        async def update_task_data(self, _task_id, updates):
            self.data.update(updates)
            self.data = sanitize_task_metadata(self.data)
            return dict(self.data)

        async def set_task_data(self, _task_id, task_data):
            self.data = sanitize_task_metadata(task_data)
            return True

    service = _service_with_settings()
    service.tasks = {}
    service.task_queue = LegacyQueue()

    restored = await service._get_or_restore_task("legacy-queued-task")

    assert restored is not None
    assert restored.embedding_config["api_key"] == "legacy-embedding-key"
    assert service.task_queue.data["priority"] == 3
    assert service.task_queue.data["retry_count"] == 1
    persisted_json = json.dumps(service.task_queue.data)
    assert "legacy-embedding-key" not in persisted_json
    assert "legacy-cv-key" not in persisted_json
    assert "legacy-es-password" not in persisted_json


@pytest.mark.asyncio
async def test_persist_redacts_worker_secret_echoed_by_provider_error():
    class CapturingQueue:
        def __init__(self):
            self.data = None

        async def update_task_data(self, _task_id, updates):
            self.data = updates
            return updates

        async def set_task_data(self, _task_id, task_data):
            self.data = task_data
            return True

    service = _service_with_settings(EMBEDDING_API_KEY="current-worker-secret")
    service.tasks = {}
    service.task_queue = CapturingQueue()
    task = DocumentParseTask(
        task_id="failed-task",
        filename="paper.pdf",
        file_size=10,
        chunk_config={},
        embedding_config={},
        store_config={},
    )
    task.errors = ["provider rejected current-worker-secret"]

    await service._persist_task(task)

    assert service.task_queue.data["errors"] == ["provider rejected [REDACTED]"]


@pytest.mark.asyncio
async def test_worker_failure_body_is_not_logged_returned_or_persisted(caplog):
    private_failure_marker = "private-provider-body"
    service = _service_with_settings()
    task = DocumentParseTask(
        task_id="failed-private-task",
        filename="paper.pdf",
        file_size=10,
        chunk_config={},
        embedding_config={},
        store_config={},
    )
    service.tasks = {task.task_id: task}
    service._get_or_restore_task = AsyncMock(return_value=task)
    service._persist_task = AsyncMock()
    service._process_chunking = AsyncMock(
        side_effect=RuntimeError(private_failure_marker)
    )

    with caplog.at_level(logging.ERROR, logger=service_module.__name__):
        result = await service.process_document_async(task.task_id)

    serialized = json.dumps(
        {
            "result": result,
            "public": task.to_dict(),
            "persisted": task.to_persisted_dict(),
        },
        ensure_ascii=False,
    )
    assert private_failure_marker not in caplog.text
    assert private_failure_marker not in serialized
    assert result["message"] == "文档解析失败"
    assert task.message == "文档解析失败"
    assert task.errors == ["文档解析失败"]


@pytest.mark.asyncio
async def test_legacy_failed_task_body_is_normalized_and_rewritten():
    private_failure_marker = "legacy-private-provider-body"
    task_data = {
        "task_id": "legacy-failed-task",
        "filename": "paper.pdf",
        "file_size": 10,
        "status": "failed",
        "progress": 0.5,
        "message": private_failure_marker,
        "errors": [private_failure_marker],
    }

    class LegacyFailedQueue:
        def __init__(self):
            self.data = dict(task_data)

        async def get_task_data(self, _task_id):
            return dict(self.data)

        async def update_task_data(self, _task_id, updates):
            self.data.update(updates)
            return dict(self.data)

        async def set_task_data(self, _task_id, task_data):
            self.data = dict(task_data)
            return True

    service = _service_with_settings()
    service.tasks = {}
    service.task_queue = LegacyFailedQueue()

    restored = await service._get_or_restore_task("legacy-failed-task")

    assert restored is not None
    assert restored.message == "文档解析失败"
    assert restored.errors == ["文档解析失败"]
    assert private_failure_marker not in json.dumps(service.task_queue.data)


@pytest.mark.asyncio
async def test_create_task_uses_atomic_idempotent_claim(tmp_path):
    service = _service_with_settings()
    service._workers_started = True
    service._payload_dir = tmp_path / "payloads"
    service._payload_dir.mkdir()
    service.tasks = {}
    service.QueuePriority = QueuePriority

    async def claim(task_id, *_args, **_kwargs):
        return task_id, True

    service.task_queue = SimpleNamespace(
        claim_idempotent_task=AsyncMock(side_effect=claim),
    )

    task_id = await service.create_task(
        filename="paper.md",
        file_content=b"markdown",
        chunk_config={},
        embedding_config={},
        store_config={},
        idempotency_key="a" * 64,
    )

    task = service.tasks[task_id]
    assert task.source_path is not None
    assert service.task_queue.claim_idempotent_task.await_count == 1
    assert service.task_queue.claim_idempotent_task.await_args.args[1] == "a" * 64
    payload_path = Path(task.source_path)
    assert payload_path.read_bytes() == b"markdown"
    assert payload_path.parent.parent == tmp_path / "payloads"
    assert len(payload_path.parent.name) == 64
    assert task_id not in str(payload_path)
    assert not list((tmp_path / "payloads" / task_id).glob(".*.tmp"))


@pytest.mark.asyncio
async def test_create_task_discards_candidate_payload_when_claim_is_reused(tmp_path):
    service = _service_with_settings()
    service._workers_started = True
    service._payload_dir = tmp_path / "payloads"
    service._payload_dir.mkdir()
    service.tasks = {}
    service.QueuePriority = QueuePriority
    service.task_queue = SimpleNamespace(
        claim_idempotent_task=AsyncMock(return_value=("existing-task", False)),
        get_task_data=AsyncMock(return_value=None),
    )

    task_id = await service.create_task(
        filename="paper.md",
        file_content=b"markdown",
        chunk_config={},
        embedding_config={},
        store_config={},
        idempotency_key="b" * 64,
    )

    assert task_id == "existing-task"
    assert list((tmp_path / "payloads").iterdir()) == []


@pytest.mark.asyncio
async def test_processing_heartbeat_stops_work_immediately_after_lease_loss():
    service = _service_with_settings()
    service._heartbeat_interval = 0.01
    processing_cancelled = asyncio.Event()

    async def process_document(_task_id, *, lease_token=None):
        assert lease_token == "lease-1"
        try:
            await asyncio.Event().wait()
        finally:
            processing_cancelled.set()

    service.process_document_async = process_document
    service.task_queue = SimpleNamespace(
        heartbeat_task=AsyncMock(return_value=False),
    )

    with pytest.raises(LeaseLostError, match="heartbeat was rejected"):
        await service._process_with_heartbeat("task-1", "lease-1")

    assert processing_cancelled.is_set()
    service.task_queue.heartbeat_task.assert_awaited_once_with("task-1", "lease-1")


@pytest.mark.asyncio
async def test_long_processing_heartbeats_and_runs_periodic_stale_recovery():
    service = _service_with_settings()
    service._heartbeat_interval = 0.01

    async def process_document(_task_id, *, lease_token=None):
        assert lease_token == "lease-1"
        await asyncio.sleep(0.025)
        return {"success": True}

    service.process_document_async = process_document
    service._recover_stale_tasks_if_due = AsyncMock(return_value=0)
    service.task_queue = SimpleNamespace(
        heartbeat_task=AsyncMock(return_value=True),
    )

    result = await service._process_with_heartbeat("task-1", "lease-1")

    assert result == {"success": True}
    assert service.task_queue.heartbeat_task.await_count >= 1
    assert service._recover_stale_tasks_if_due.await_count >= 1


@pytest.mark.asyncio
async def test_stale_recovery_checks_payload_before_atomic_transition(tmp_path):
    existing_payload = tmp_path / "existing.md"
    existing_payload.write_text("payload")
    leases = [
        TaskLease("with-payload", "lease-1"),
        TaskLease("missing-payload", "lease-2"),
    ]
    task_data = {
        "with-payload": {"source_path": str(existing_payload)},
        "missing-payload": {"source_path": str(tmp_path / "missing.md")},
    }

    async def get_task_data(task_id):
        return task_data[task_id]

    async def recover(task_id, _token, *, payload_available):
        return "requeued" if payload_available else "failed"

    service = _service_with_settings()
    service.tasks = {}
    service.task_queue = SimpleNamespace(
        get_stale_task_leases=AsyncMock(return_value=leases),
        get_task_data=AsyncMock(side_effect=get_task_data),
        recover_stale_task=AsyncMock(side_effect=recover),
    )

    assert await service._recover_stale_tasks() == 2
    calls = service.task_queue.recover_stale_task.await_args_list
    assert calls[0].kwargs == {"payload_available": True}
    assert calls[1].kwargs == {"payload_available": False}


@pytest.mark.asyncio
async def test_shutdown_requeues_owned_lease_without_deleting_payload(tmp_path):
    payload = tmp_path / "payload.md"
    payload.write_text("payload")
    task = DocumentParseTask(
        task_id="shutdown-task",
        filename=payload.name,
        file_size=payload.stat().st_size,
        chunk_config={},
        embedding_config={},
        store_config={},
    )
    task.source_path = str(payload)

    service = _service_with_settings()
    service.tasks = {task.task_id: task}
    service._shutdown_event = asyncio.Event()
    service._worker_tasks = set()
    service._active_leases = {task.task_id: "lease-token"}
    service.task_queue = SimpleNamespace(
        get_task_data=AsyncMock(return_value=task.to_persisted_dict()),
        requeue_task=AsyncMock(return_value="requeued"),
        close=AsyncMock(),
    )

    await service.shutdown()

    service.task_queue.requeue_task.assert_awaited_once_with(
        task.task_id,
        "lease-token",
        payload_available=True,
    )
    service.task_queue.close.assert_awaited_once()
    assert payload.read_text() == "payload"
    assert service._active_leases == {}


@pytest.mark.asyncio
async def test_shutdown_cancels_active_worker_and_requeues_its_lease(tmp_path):
    payload = tmp_path / "active.md"
    payload.write_text("payload")
    task = DocumentParseTask(
        task_id="active-task",
        filename=payload.name,
        file_size=payload.stat().st_size,
        chunk_config={},
        embedding_config={},
        store_config={},
    )
    task.source_path = str(payload)
    processing_started = asyncio.Event()

    async def process_document(_task_id, *, lease_token=None):
        assert lease_token == "lease-token"
        processing_started.set()
        await asyncio.Event().wait()

    service = _service_with_settings()
    service.tasks = {task.task_id: task}
    service._shutdown_event = asyncio.Event()
    service._worker_tasks = set()
    service._active_leases = {}
    service._heartbeat_interval = 60
    service.process_document_async = process_document
    service.task_queue = SimpleNamespace(
        get_task_data=AsyncMock(return_value=task.to_persisted_dict()),
        heartbeat_task=AsyncMock(return_value=True),
        requeue_task=AsyncMock(return_value="requeued"),
        close=AsyncMock(),
    )
    worker = asyncio.create_task(
        service._process_lease("worker-1", task.task_id, "lease-token")
    )
    service._worker_tasks.add(worker)
    await processing_started.wait()

    await service.shutdown()

    service.task_queue.requeue_task.assert_awaited_once_with(
        task.task_id,
        "lease-token",
        payload_available=True,
    )
    assert worker.done()
    assert payload.read_text() == "payload"
    assert service._active_leases == {}


@pytest.mark.asyncio
async def test_leased_persistence_rejects_stale_worker_metadata():
    task = DocumentParseTask(
        task_id="stale-task",
        filename="paper.md",
        file_size=8,
        chunk_config={},
        embedding_config={},
        store_config={},
    )
    service = _service_with_settings()
    service.tasks = {}
    service._active_leases = {task.task_id: "old-lease"}
    service.task_queue = SimpleNamespace(
        update_task_data=AsyncMock(return_value=None),
    )

    with pytest.raises(LeaseLostError, match="no longer owns its lease"):
        await service._persist_task(task)

    service.task_queue.update_task_data.assert_awaited_once()
    assert service.task_queue.update_task_data.await_args.kwargs == {
        "lease_token": "old-lease"
    }
