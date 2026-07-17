import asyncio
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

os.environ["DEBUG"] = "false"

import pytest
import httpx
from fastapi import HTTPException

from modules.creative_workshop import controller
from modules.creative_workshop.paper_translation_service import (
    QUEUE_ATTEMPTS_KEY,
    QUEUE_GENERATIONS_KEY,
    QUEUE_LEASE_TOKENS_KEY,
    QUEUE_PENDING_KEY,
    QUEUE_PAYLOADS_KEY,
    QUEUE_PROCESSING_KEY,
    QUEUE_RECONCILE_CURSOR_KEY,
    QUEUE_SCHEDULED_KEY,
    PaperTranslationLeaseLost,
    PaperTranslationQueueItem,
    PaperTranslationQueueWorker,
    PaperTranslationService,
    PaperTranslationTask,
    PaperTranslationTaskLease,
    _QUEUE_ACK_SCRIPT,
    _QUEUE_ACQUIRE_SCRIPT,
    _QUEUE_CANCEL_SCRIPT,
    _QUEUE_ENQUEUE_SCRIPT,
    _QUEUE_HEARTBEAT_SCRIPT,
    _QUEUE_PROMOTE_SCRIPT,
    _QUEUE_RECOVER_SCRIPT,
    _QUEUE_RELEASE_SCRIPT,
    _QUEUE_RETRY_SCRIPT,
    _extract_translated_markdown_path,
    _normalize_translated_markdown_artifact_path,
    _normalize_scoped_pdf_resource_url,
    _remove_mermaid_diagram_blocks,
    _safe_pdf_url_fetcher,
)
from utils.mineru_service import MineruService


@pytest.mark.asyncio
async def test_generate_image_requires_configured_api_key(monkeypatch):
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_API_KEY", "")
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await controller.generate_image(
            request=controller.ImageGenerationRequest(prompt="minimal icon"),
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "IMAGE_API_NOT_CONFIGURED"
    controller.record_user_prompt_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_image_posts_openai_compatible_payload(monkeypatch):
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_IMAGE_BASE_URL",
        "https://example.test/v1",
    )
    monkeypatch.setattr(
        controller.settings, "CREATIVE_WORKSHOP_IMAGE_API_KEY", "test-key"
    )
    monkeypatch.setattr(
        controller.settings, "CREATIVE_WORKSHOP_IMAGE_MODEL", "gpt-image-2"
    )
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_TIMEOUT", 12.0)

    calls = []

    class _Response:
        headers = {}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b'{"data":[{"b64_json":"ZmFrZS1pbWFnZQ=="}]}'

    class _StreamContext:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            calls.append(("init", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, headers, json):
            calls.append(("stream", method, url, headers, json))
            return _StreamContext()

    monkeypatch.setattr(controller.httpx, "AsyncClient", _Client)

    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    result = await controller.generate_image(
        request=controller.ImageGenerationRequest(
            prompt="  minimal icon  ",
            size="1536x1024",
            quality="medium",
            output_format="jpeg",
            output_compression=80,
        ),
        current_user=SimpleNamespace(id=uuid4()),
    )

    assert result.b64_json == "ZmFrZS1pbWFnZQ=="
    assert result.mime_type == "image/jpeg"
    assert calls[0] == (
        "init",
        {"timeout": 12.0, "trust_env": False, "follow_redirects": False},
    )
    assert calls[1][2] == "https://example.test/v1/images/generations"
    assert calls[1][3]["Authorization"] == "Bearer test-key"
    assert calls[1][3]["Accept-Encoding"] == "identity"
    assert calls[1][4] == {
        "model": "gpt-image-2",
        "prompt": "minimal icon",
        "size": "1536x1024",
        "quality": "medium",
        "output_format": "jpeg",
        "output_compression": 80,
    }
    controller.record_user_prompt_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_image_provider_error_body_and_url_are_not_exposed_or_logged(
    monkeypatch, caplog
):
    secret_marker = "private-provider-body-and-query-secret"
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_IMAGE_API_KEY",
        "configured-provider-key",
    )
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_IMAGE_BASE_URL",
        "https://example.test/v1",
    )

    request = httpx.Request(
        "POST",
        f"https://example.test/v1/images/generations?token={secret_marker}",
    )
    response = httpx.Response(500, text=secret_marker, request=request)

    class _StreamContext:
        async def __aenter__(self):
            return response

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, headers, json):
            return _StreamContext()

    monkeypatch.setattr(controller.httpx, "AsyncClient", _Client)

    with (
        caplog.at_level(logging.WARNING, logger=controller.__name__),
        pytest.raises(HTTPException) as exc_info,
    ):
        await controller._post_image_provider_json(
            path="/images/generations",
            payload={"prompt": "private prompt"},
        )

    assert exc_info.value.status_code == 502
    assert secret_marker not in str(exc_info.value.detail)
    assert secret_marker not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"content-length": "6"}, []),
        ({}, [b"123", b"456"]),
    ],
)
async def test_image_provider_response_limit_checks_declared_and_streamed_bytes(
    monkeypatch, headers, chunks
):
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_IMAGE_MAX_RESPONSE_BYTES",
        5,
    )

    class _Response:
        async def aiter_bytes(self):
            for chunk in chunks:
                yield chunk

    response = _Response()
    response.headers = headers

    with pytest.raises(HTTPException) as exc_info:
        await controller._read_image_provider_json(response)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"]["code"] == "IMAGE_PROVIDER_BAD_RESPONSE"


@pytest.mark.asyncio
async def test_generate_image_records_image_prompt(monkeypatch, tmp_path):
    user_id = uuid4()
    monkeypatch.setattr(controller.settings, "AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(controller.settings, "AUDIT_LOG_INCLUDE_PROMPTS", False)
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_IMAGE_BASE_URL",
        "https://example.test/v1",
    )
    monkeypatch.setattr(
        controller.settings, "CREATIVE_WORKSHOP_IMAGE_API_KEY", "test-key"
    )
    monkeypatch.setattr(
        controller.settings, "CREATIVE_WORKSHOP_IMAGE_MODEL", "gpt-image-2"
    )

    class _Response:
        headers = {}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b'{"data":[{"b64_json":"ZmFrZS1pbWFnZQ=="}]}'

    class _StreamContext:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, headers, json):
            return _StreamContext()

    monkeypatch.setattr(controller.httpx, "AsyncClient", _Client)

    await controller.generate_image(
        request=controller.ImageGenerationRequest(
            prompt="赛博城市夜景",
            size="1024x1536",
            quality="high",
            output_format="png",
            output_compression=None,
        ),
        current_user=SimpleNamespace(
            id=user_id, name="alice", email="alice@example.com"
        ),
    )

    [log_file] = list(tmp_path.glob("*/user-*.jsonl"))
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["event_type"] == "image2_prompt"
    assert record["user"]["id"] == str(user_id)
    assert "prompt" not in record
    assert record["prompt_length"] == len("赛博城市夜景")
    assert len(record["prompt_fingerprint"]) == 64
    assert record["metadata"]["model"] == "gpt-image-2"
    assert record["metadata"]["size"] == "1024x1536"


class _FakeUpload:
    def __init__(
        self, *, filename: str, content: bytes, content_type: str = "application/pdf"
    ):
        self.filename = filename
        self.content_type = content_type
        self._file = io.BytesIO(content)

    async def read(self, size: int = -1):
        return self._file.read(size)


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.strings: dict[str, str] = {}
        self._eval_lock = asyncio.Lock()

    async def lpush(self, key: str, value: str):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def rpop(self, key: str):
        values = self.lists.setdefault(key, [])
        return values.pop() if values else None

    async def brpoplpush(self, source: str, destination: str, timeout: int = 0):
        value = await self.rpop(source)
        if value is None:
            return None
        await self.lpush(destination, value)
        return value

    async def lrem(self, key: str, count: int, value: str):
        values = self.lists.setdefault(key, [])
        removed = 0
        next_values = []
        for item in values:
            if item == value and (count <= 0 or removed < count):
                removed += 1
                continue
            next_values.append(item)
        self.lists[key] = next_values
        return removed

    async def lrange(self, key: str, start: int, end: int):
        values = self.lists.setdefault(key, [])
        stop = None if end == -1 else end + 1
        return values[start:stop]

    async def hget(self, key: str, field: str):
        return self.hashes.setdefault(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hdel(self, key: str, field: str):
        return int(self.hashes.setdefault(key, {}).pop(field, None) is not None)

    async def get(self, key: str):
        return self.strings.get(key)

    async def set(self, key: str, value: str):
        self.strings[key] = value
        return True

    async def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            deleted += int(self.strings.pop(key, None) is not None)
            deleted += int(self.lists.pop(key, None) is not None)
            deleted += int(self.hashes.pop(key, None) is not None)
            deleted += int(self.sorted_sets.pop(key, None) is not None)
        return deleted

    async def zadd(self, key: str, mapping: dict[str, float]):
        values = self.sorted_sets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            added += int(member not in values)
            values[member] = float(score)
        return added

    async def zrangebyscore(self, key: str, min, max):
        values = self.sorted_sets.setdefault(key, {})
        minimum = float(min)
        maximum = float(max)
        return [
            member for member, score in values.items() if minimum <= score <= maximum
        ]

    async def zrem(self, key: str, member: str):
        return int(self.sorted_sets.setdefault(key, {}).pop(member, None) is not None)

    async def zscore(self, key: str, member: str):
        return self.sorted_sets.setdefault(key, {}).get(member)

    async def zcard(self, key: str):
        return len(self.sorted_sets.setdefault(key, {}))

    async def eval(self, script: str, numkeys: int, *args):
        async with self._eval_lock:
            return await self._eval_unlocked(script, numkeys, *args)

    async def _eval_unlocked(self, script: str, numkeys: int, *args):
        assert numkeys == 7
        (
            pending_key,
            processing_key,
            scheduled_key,
            payloads_key,
            attempts_key,
            tokens_key,
            generations_key,
        ) = args[:7]
        argv = args[7:]

        if script == _QUEUE_ENQUEUE_SCRIPT:
            task_id, payload, attempt, ready_at = argv
            if any(
                task_id in self.sorted_sets.setdefault(key, {})
                for key in (pending_key, processing_key, scheduled_key)
            ):
                return 0
            await self.hset(payloads_key, task_id, payload)
            await self.hset(attempts_key, task_id, str(attempt))
            await self.zadd(pending_key, {task_id: float(ready_at)})
            return 1

        if script == _QUEUE_ACQUIRE_SCRIPT:
            now, lease_until, token = argv
            due = sorted(
                (
                    (score, task_id)
                    for task_id, score in self.sorted_sets.setdefault(
                        pending_key, {}
                    ).items()
                    if score <= float(now)
                )
            )
            for _, task_id in due[:20]:
                await self.zrem(pending_key, task_id)
                payload = await self.hget(payloads_key, task_id)
                if payload is None:
                    await self.hdel(attempts_key, task_id)
                    continue
                attempt = await self.hget(attempts_key, task_id) or "1"
                await self.zadd(processing_key, {task_id: float(lease_until)})
                await self.hset(tokens_key, task_id, token)
                generation = int(await self.hget(generations_key, task_id) or 0) + 1
                await self.hset(generations_key, task_id, str(generation))
                return [task_id, payload, attempt, generation]
            return []

        task_id = str(argv[0])
        token = str(argv[1]) if len(argv) > 1 else ""
        current_token = await self.hget(tokens_key, task_id)
        current_deadline = await self.zscore(processing_key, task_id)

        if script == _QUEUE_HEARTBEAT_SCRIPT:
            now, lease_until = map(float, argv[2:4])
            if (
                current_token != token
                or current_deadline is None
                or current_deadline < now
            ):
                return 0
            await self.zadd(processing_key, {task_id: lease_until})
            return 1

        if script in {_QUEUE_ACK_SCRIPT, _QUEUE_CANCEL_SCRIPT}:
            now = float(argv[2])
            if (
                current_token != token
                or current_deadline is None
                or current_deadline < now
            ):
                return 0
            for key in (pending_key, processing_key, scheduled_key):
                await self.zrem(key, task_id)
            for key in (payloads_key, attempts_key, tokens_key, generations_key):
                await self.hdel(key, task_id)
            return 1

        if script == _QUEUE_RELEASE_SCRIPT:
            now = float(argv[2])
            if (
                current_token != token
                or current_deadline is None
                or current_deadline < now
            ):
                return 0
            await self.zrem(processing_key, task_id)
            await self.hdel(tokens_key, task_id)
            await self.zadd(pending_key, {task_id: now})
            return 1

        if script == _QUEUE_RETRY_SCRIPT:
            now, ready_at = map(float, argv[2:4])
            next_attempt = str(argv[4])
            if (
                current_token != token
                or current_deadline is None
                or current_deadline < now
            ):
                return 0
            await self.zrem(processing_key, task_id)
            await self.hdel(tokens_key, task_id)
            await self.hset(attempts_key, task_id, next_attempt)
            await self.zadd(scheduled_key, {task_id: ready_at})
            return 1

        if script == _QUEUE_PROMOTE_SCRIPT:
            now, limit = float(argv[0]), int(argv[1])
            due = sorted(
                (
                    (score, member)
                    for member, score in self.sorted_sets.setdefault(
                        scheduled_key, {}
                    ).items()
                    if score <= now
                )
            )
            for _, member in due[:limit]:
                await self.zrem(scheduled_key, member)
                await self.zadd(pending_key, {member: now})
            return len(due[:limit])

        if script == _QUEUE_RECOVER_SCRIPT:
            now, limit = float(argv[0]), int(argv[1])
            expired = sorted(
                (
                    (score, member)
                    for member, score in self.sorted_sets.setdefault(
                        processing_key, {}
                    ).items()
                    if score <= now
                )
            )
            for _, member in expired[:limit]:
                await self.zrem(processing_key, member)
                await self.hdel(tokens_key, member)
                attempt = int(await self.hget(attempts_key, member) or 1) + 1
                await self.hset(attempts_key, member, str(attempt))
                await self.zadd(pending_key, {member: now})
            return len(expired[:limit])

        raise AssertionError("Unexpected Redis Lua script")

    def pipeline(self, transaction: bool = True):
        redis_client = self

        class _Pipeline:
            def __init__(self):
                self.operations = []

            def zadd(self, key, mapping):
                self.operations.append(("zadd", key, mapping))
                return self

            def lrem(self, key, count, value):
                self.operations.append(("lrem", key, count, value))
                return self

            def hdel(self, key, field):
                self.operations.append(("hdel", key, field))
                return self

            async def execute(self):
                results = []
                for operation in self.operations:
                    if operation[0] == "zadd":
                        results.append(
                            await redis_client.zadd(operation[1], operation[2])
                        )
                    elif operation[0] == "lrem":
                        results.append(
                            await redis_client.lrem(
                                operation[1], operation[2], operation[3]
                            )
                        )
                    elif operation[0] == "hdel":
                        results.append(
                            await redis_client.hdel(operation[1], operation[2])
                        )
                return results

        return _Pipeline()


@pytest.mark.asyncio
async def test_save_pdf_upload_streams_to_destination(tmp_path):
    destination = tmp_path / "source.pdf"

    size = await controller._save_pdf_upload(
        _FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
        destination,
    )

    assert size == len(b"%PDF-1.4\nfake")
    assert destination.read_bytes() == b"%PDF-1.4\nfake"


@pytest.mark.asyncio
async def test_save_pdf_upload_rejects_oversized_file_before_full_read(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(controller.settings, "MAX_UPLOAD_SIZE", 8)
    destination = tmp_path / "source.pdf"

    with pytest.raises(HTTPException) as exc_info:
        await controller._save_pdf_upload(
            _FakeUpload(
                filename="paper.pdf",
                content=b"%PDF-1.4\nmore-than-eight-bytes",
            ),
            destination,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "FILE_TOO_LARGE"
    assert not destination.exists()


@pytest.mark.asyncio
async def test_create_paper_translation_task_enqueues_redis_job(monkeypatch, tmp_path):
    user_id = uuid4()
    task = PaperTranslationTask(
        task_id="task-1",
        owner_id=str(user_id),
        filename="paper.pdf",
        thread_id="paper-translation-task-1",
        status="queued",
        created_at="2026-05-28T00:00:00+00:00",
        updated_at="2026-05-28T00:00:00+00:00",
    )

    class _Service:
        storage_root = tmp_path / "storage"
        create_task = AsyncMock(return_value=task)
        attach_source_pdf = AsyncMock(return_value=task)
        mark_task_failed = AsyncMock()

        def source_pdf_path(self, *, owner_id, task_id):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            return tmp_path / "task-1" / "source.pdf"

        async def enqueue_translation_task(self, redis_client, item):
            await redis_client.lpush(QUEUE_PENDING_KEY, json.dumps(item.__dict__))

        async def get_task(self, *, owner_id, task_id):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            return task

        def build_response_payload(self, payload_task):
            assert payload_task is task
            return {
                "task_id": task.task_id,
                "status": task.status,
                "filename": task.filename,
                "thread_id": task.thread_id,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "error": None,
            }

    service = _Service()
    redis_client = _FakeRedis()
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: service)
    monkeypatch.setattr(
        controller, "get_redis_client", AsyncMock(return_value=redis_client)
    )
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    response = await controller.create_paper_translation_task(
        file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
        current_user=SimpleNamespace(
            id=user_id, name="alice", email="alice@example.com"
        ),
    )

    assert response.task_id == "task-1"
    assert response.status == "queued"
    service.create_task.assert_awaited_once_with(
        owner_id=str(user_id), filename="paper.pdf", model_name=None
    )
    service.attach_source_pdf.assert_awaited_once_with(
        owner_id=str(user_id),
        task_id="task-1",
        source_pdf_path=tmp_path / "task-1" / "source.pdf",
    )
    assert (tmp_path / "task-1" / "source.pdf").read_bytes() == b"%PDF-1.4\nfake"
    assert not list((tmp_path / "storage" / "_incoming").glob("*.pdf"))
    [queued] = redis_client.lists[QUEUE_PENDING_KEY]
    queued_payload = json.loads(queued)
    assert queued_payload["owner_id"] == str(user_id)
    assert queued_payload["task_id"] == "task-1"
    assert queued_payload["filename"] == "paper.pdf"
    assert queued_payload["model_name"] is None
    controller.record_user_prompt_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_paper_translation_task_preserves_selected_model(
    monkeypatch, tmp_path
):
    user_id = uuid4()
    task = PaperTranslationTask(
        task_id="task-1",
        owner_id=str(user_id),
        filename="paper.pdf",
        thread_id="paper-translation-task-1",
        status="queued",
        created_at="2026-05-28T00:00:00+00:00",
        updated_at="2026-05-28T00:00:00+00:00",
        model_name="model-custom",
    )

    class _Service:
        storage_root = tmp_path / "storage"
        create_task = AsyncMock(return_value=task)
        attach_source_pdf = AsyncMock(return_value=task)
        mark_task_failed = AsyncMock()

        def source_pdf_path(self, *, owner_id, task_id):
            return tmp_path / "task-1" / "source.pdf"

        async def enqueue_translation_task(self, redis_client, item):
            await redis_client.lpush(QUEUE_PENDING_KEY, json.dumps(item.__dict__))

        async def get_task(self, *, owner_id, task_id):
            return task

        def build_response_payload(self, payload_task):
            return {
                "task_id": task.task_id,
                "status": task.status,
                "filename": task.filename,
                "thread_id": task.thread_id,
                "model_name": task.model_name,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "error": None,
            }

    service = _Service()
    redis_client = _FakeRedis()
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: service)
    monkeypatch.setattr(
        controller, "get_redis_client", AsyncMock(return_value=redis_client)
    )
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    response = await controller.create_paper_translation_task(
        file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
        model_name="  model-custom  ",
        current_user=SimpleNamespace(
            id=user_id, name="alice", email="alice@example.com"
        ),
    )

    assert response.model_name == "model-custom"
    service.create_task.assert_awaited_once_with(
        owner_id=str(user_id),
        filename="paper.pdf",
        model_name="model-custom",
    )
    [queued] = redis_client.lists[QUEUE_PENDING_KEY]
    queued_payload = json.loads(queued)
    assert queued_payload["model_name"] == "model-custom"
    event = controller.record_user_prompt_event.await_args.kwargs
    assert event["metadata"]["model_name"] == "model-custom"
    service.mark_task_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_paper_translation_task_rejects_invalid_pdf_before_task_creation(
    monkeypatch, tmp_path
):
    user_id = uuid4()

    class _Service:
        storage_root = tmp_path / "storage"
        create_task = AsyncMock()

    service = _Service()
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: service)
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await controller.create_paper_translation_task(
            file=_FakeUpload(filename="paper.pdf", content=b"not a pdf"),
            current_user=SimpleNamespace(
                id=user_id, name="alice", email="alice@example.com"
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "INVALID_PDF"
    service.create_task.assert_not_awaited()
    controller.record_user_prompt_event.assert_not_awaited()
    assert not list((tmp_path / "storage" / "_incoming").glob("*.pdf"))


@pytest.mark.asyncio
async def test_create_paper_translation_task_reports_unwritable_storage(
    monkeypatch, tmp_path
):
    user_id = uuid4()

    class _Service:
        storage_root = tmp_path / "storage"
        create_task = AsyncMock()

    service = _Service()
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: service)
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await controller.create_paper_translation_task(
            file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
            current_user=SimpleNamespace(
                id=user_id, name="alice", email="alice@example.com"
            ),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "STORAGE_UNAVAILABLE"
    service.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_paper_translation_task_marks_failed_when_queue_unavailable(
    monkeypatch, tmp_path
):
    user_id = uuid4()
    task = PaperTranslationTask(
        task_id="task-1",
        owner_id=str(user_id),
        filename="paper.pdf",
        thread_id="paper-translation-task-1",
        status="queued",
        created_at="2026-05-28T00:00:00+00:00",
        updated_at="2026-05-28T00:00:00+00:00",
    )

    class _Service:
        storage_root = tmp_path / "storage"
        create_task = AsyncMock(return_value=task)
        attach_source_pdf = AsyncMock(return_value=task)
        mark_task_failed = AsyncMock(return_value=task)

        def source_pdf_path(self, *, owner_id, task_id):
            return tmp_path / "task-1" / "source.pdf"

        async def enqueue_translation_task(self, redis_client, item):
            raise RuntimeError("redis down")

    service = _Service()
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: service)
    monkeypatch.setattr(
        controller, "get_redis_client", AsyncMock(return_value=_FakeRedis())
    )
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await controller.create_paper_translation_task(
            file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
            current_user=SimpleNamespace(
                id=user_id, name="alice", email="alice@example.com"
            ),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "QUEUE_UNAVAILABLE"
    service.mark_task_failed.assert_awaited_once_with(
        owner_id=str(user_id),
        task_id="task-1",
        error="翻译任务入队失败，请稍后重试",
    )


@pytest.mark.asyncio
async def test_paper_translation_service_runs_mineru_then_agent(monkeypatch, tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    source_pdf_path = service.source_pdf_path(owner_id=owner_id, task_id=task.task_id)
    source_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    source_pdf_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id=owner_id, task_id=task.task_id, source_pdf_path=source_pdf_path
    )

    monkeypatch.setattr(
        MineruService,
        "convert_document",
        AsyncMock(return_value={"batch_id": "batch-1"}),
    )
    monkeypatch.setattr(
        MineruService,
        "get_task_status",
        AsyncMock(return_value={"status": "completed"}),
    )
    monkeypatch.setattr(
        MineruService,
        "get_content_with_assets",
        AsyncMock(
            return_value=SimpleNamespace(
                markdown="# Title\n\nEnglish text.\n\n![](images/fig.jpg)",
                assets={"images/fig.jpg": b"fake-image"},
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_translate_markdown_with_agent",
        AsyncMock(
            return_value="# 标题\n\n中文正文。\n\n![](images/fig.jpg)\n\n## References\nSmith, 2020."
        ),
    )

    await service.run_translation_task(
        owner_id=owner_id,
        task_id=task.task_id,
        filename="demo.pdf",
        source_pdf_path=str(source_pdf_path),
    )

    completed = await service.get_task(owner_id=owner_id, task_id=task.task_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.mineru_batch_id == "batch-1"
    assert completed.source_markdown_path
    assert completed.translated_markdown_path
    assert (
        "English text" in open(completed.source_markdown_path, encoding="utf-8").read()
    )
    assert (
        "中文正文" in open(completed.translated_markdown_path, encoding="utf-8").read()
    )
    assert (
        tmp_path / owner_id / task.task_id / "assets" / "images" / "fig.jpg"
    ).read_bytes() == b"fake-image"
    service._translate_markdown_with_agent.assert_awaited_once_with(
        owner_id=owner_id,
        task_id=task.task_id,
        filename="demo.pdf",
        markdown="# Title\n\nEnglish text.\n\n![](images/fig.jpg)",
    )


@pytest.mark.asyncio
async def test_paper_translation_task_thread_id_is_uuid(tmp_path):
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=str(uuid4()), filename="demo.pdf")

    assert str(UUID(task.thread_id)) == task.thread_id


@pytest.mark.asyncio
async def test_paper_translation_agent_uses_skill_and_downloads_artifact(
    monkeypatch, tmp_path
):
    owner_id = str(uuid4())
    captured: dict[str, object] = {}

    class _RuntimeService:
        langgraph_url = "http://langgraph"

        def build_thread_id(self, thread_id):
            return str(thread_id)

        async def ensure_thread_exists(self, thread_id):
            return {}

        async def resolve_assistant_id(self):
            return "assistant-1"

        async def list_runtime_models(self):
            return [{"name": "runtime-model-1"}]

        async def upload_bytes(self, **kwargs):
            return {
                "filename": kwargs["filename"],
                "size": len(kwargs["data"]),
                "virtual_path": f"/mnt/user-data/uploads/{kwargs['filename']}",
            }

        def build_run_request_template(self, **kwargs):
            captured["template_kwargs"] = kwargs
            return {
                "assistant_id": kwargs["assistant_id"],
                "context": {
                    "thread_id": kwargs["thread_id"],
                    "disable_model_streaming": kwargs.get(
                        "disable_model_streaming", False
                    ),
                },
                "input": {"messages": []},
            }

        def build_run_stream_path(self, thread_id):
            return f"/threads/{thread_id}/runs/stream"

        async def download_thread_artifact_text(self, thread_id, virtual_path):
            captured["downloaded_artifact"] = (thread_id, virtual_path)
            return "# 标题\n\n中文正文。"

    class _Response:
        def raise_for_status(self):
            return None

        async def aiter_text(self):
            yield (
                "event: values\n"
                'data: {"messages":[{"type":"ai","content":"{\\"translated_markdown_path\\":\\"/mnt/user-data/outputs/demo.zh.md\\"}"}]}\n\n'
            )

    class _StreamContext:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, headers, json):
            captured["stream_request"] = json
            return _StreamContext()

    monkeypatch.setattr(
        "modules.creative_workshop.paper_translation_service.httpx.AsyncClient",
        _Client,
    )

    class _ModelConfigService:
        def __init__(self, db):
            pass

        async def resolve_selected_model(self, **kwargs):
            captured["model_resolution_kwargs"] = kwargs
            return {
                "runtime_model_name": "model-1",
                "dynamic_model_token": "token-1",
            }

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(
        "modules.creative_workshop.paper_translation_service.ModelConfigService",
        _ModelConfigService,
    )

    service = PaperTranslationService(
        storage_root=tmp_path,
        runtime_service=_RuntimeService(),
        model_config_session_factory=lambda: _Session(),
    )
    task = await service.create_task(
        owner_id=owner_id, filename="demo.pdf", model_name="model-custom"
    )

    result = await service._translate_markdown_with_agent(
        owner_id=owner_id,
        task_id=task.task_id,
        filename="demo.pdf",
        markdown="# Title",
    )

    assert result == "# 标题\n\n中文正文。"
    model_resolution_kwargs = captured["model_resolution_kwargs"]
    assert str(model_resolution_kwargs["user_id"]) == owner_id
    assert model_resolution_kwargs["selected_model_name"] == "model-custom"
    assert model_resolution_kwargs["runtime_models"] == [{"name": "runtime-model-1"}]
    assert model_resolution_kwargs["thread_id"] == task.thread_id
    assert captured["template_kwargs"]["model_name"] == "model-1"
    assert captured["template_kwargs"].get("disable_model_streaming", False) is False
    assert captured["template_kwargs"]["recursion_limit"] == (
        controller.settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_RECURSION_LIMIT
    )
    assert captured["stream_request"]["context"]["disable_model_streaming"] is False
    assert captured["stream_request"]["context"]["dynamic_model_token"] == "token-1"
    prompt = captured["stream_request"]["input"]["messages"][0]["content"]
    assert "paper-translation" in prompt
    assert "/mnt/user-data/outputs/demo.zh.md" in prompt
    assert "禁止使用 bash" in prompt
    assert captured["downloaded_artifact"] == (
        task.thread_id,
        "/mnt/user-data/outputs/demo.zh.md",
    )


@pytest.mark.asyncio
async def test_paper_translation_agent_continues_after_recursion_limit_with_progress(
    monkeypatch, tmp_path, caplog
):
    owner_id = str(uuid4())
    secret_marker = "private-runtime-error-payload"
    stream_requests: list[dict] = []
    download_calls: list[tuple[str, str]] = []
    stream_events = iter(
        [
            (
                "event: error\n"
                f'data: {{"error":"GraphRecursionError","message":"{secret_marker}"}}\n\n'
            ),
            (
                "event: values\n"
                'data: {"messages":[{"type":"ai","content":"{\\"translated_markdown_path\\":'
                '\\"/mnt/user-data/outputs/demo.zh.md\\"}"}]}\n\n'
            ),
        ]
    )

    class _RuntimeService:
        langgraph_url = "http://langgraph"

        def build_thread_id(self, thread_id):
            return str(thread_id)

        async def ensure_thread_exists(self, thread_id):
            return {}

        async def resolve_assistant_id(self):
            return "assistant-1"

        async def list_runtime_models(self):
            return [{"name": "model-1"}]

        async def upload_bytes(self, **kwargs):
            return {
                "filename": kwargs["filename"],
                "size": len(kwargs["data"]),
                "virtual_path": f"/mnt/user-data/uploads/{kwargs['filename']}",
            }

        def build_run_request_template(self, **kwargs):
            return {
                "assistant_id": kwargs["assistant_id"],
                "context": {"thread_id": kwargs["thread_id"]},
                "config": {"recursion_limit": kwargs["recursion_limit"]},
                "input": {"messages": []},
            }

        def build_run_stream_path(self, thread_id):
            return f"/threads/{thread_id}/runs/stream"

        async def download_thread_artifact_text(self, thread_id, virtual_path):
            download_calls.append((thread_id, virtual_path))
            return "# 部分译文" if len(download_calls) == 1 else "# 完整译文"

    class _Response:
        def __init__(self, event_text):
            self.event_text = event_text

        def raise_for_status(self):
            return None

        async def aiter_text(self):
            yield self.event_text

    class _StreamContext:
        def __init__(self, event_text):
            self.response = _Response(event_text)

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, headers, json):
            stream_requests.append(json)
            return _StreamContext(next(stream_events))

    monkeypatch.setattr(
        "modules.creative_workshop.paper_translation_service.httpx.AsyncClient",
        _Client,
    )
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_RECURSION_LIMIT",
        17,
    )
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_MAX_CONTINUATIONS",
        2,
    )

    service = PaperTranslationService(
        storage_root=tmp_path, runtime_service=_RuntimeService()
    )
    monkeypatch.setattr(
        service,
        "_resolve_agent_model_context",
        AsyncMock(return_value=("model-1", None)),
    )
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")

    with caplog.at_level(
        logging.WARNING,
        logger="modules.creative_workshop.paper_translation_service",
    ):
        result = await service._translate_markdown_with_agent(
            owner_id=owner_id,
            task_id=task.task_id,
            filename="demo.pdf",
            markdown="# Title",
        )

    assert result == "# 完整译文"
    assert len(stream_requests) == 2
    assert secret_marker not in caplog.text
    assert all(
        request["config"]["recursion_limit"] == 17 for request in stream_requests
    )
    assert stream_requests[1]["input"]["messages"][0]["content"].startswith(
        "继续尚未完成的论文翻译任务"
    )
    assert download_calls == [
        (task.thread_id, "/mnt/user-data/outputs/demo.zh.md"),
        (task.thread_id, "/mnt/user-data/outputs/demo.zh.md"),
    ]


@pytest.mark.asyncio
async def test_paper_translation_agent_stops_continuation_without_progress(
    monkeypatch, tmp_path
):
    owner_id = str(uuid4())
    stream_count = 0

    class _RuntimeService:
        langgraph_url = "http://langgraph"

        def build_thread_id(self, thread_id):
            return str(thread_id)

        async def ensure_thread_exists(self, thread_id):
            return {}

        async def resolve_assistant_id(self):
            return "assistant-1"

        async def list_runtime_models(self):
            return [{"name": "model-1"}]

        async def upload_bytes(self, **kwargs):
            return {"filename": kwargs["filename"], "size": len(kwargs["data"])}

        def build_run_request_template(self, **kwargs):
            return {"context": {}, "input": {"messages": []}}

        def build_run_stream_path(self, thread_id):
            return f"/threads/{thread_id}/runs/stream"

        async def download_thread_artifact_text(self, thread_id, virtual_path):
            return "# 没有变化的部分译文"

    class _Response:
        def raise_for_status(self):
            return None

        async def aiter_text(self):
            yield (
                "event: error\n"
                'data: {"error":"GraphRecursionError","message":"Recursion limit reached"}\n\n'
            )

    class _StreamContext:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, headers, json):
            nonlocal stream_count
            stream_count += 1
            return _StreamContext()

    monkeypatch.setattr(
        "modules.creative_workshop.paper_translation_service.httpx.AsyncClient",
        _Client,
    )
    monkeypatch.setattr(
        controller.settings,
        "CREATIVE_WORKSHOP_PAPER_TRANSLATION_AGENT_MAX_CONTINUATIONS",
        2,
    )

    service = PaperTranslationService(
        storage_root=tmp_path, runtime_service=_RuntimeService()
    )
    monkeypatch.setattr(
        service,
        "_resolve_agent_model_context",
        AsyncMock(return_value=("model-1", None)),
    )
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")

    with pytest.raises(RuntimeError, match="停止产生有效进展"):
        await service._translate_markdown_with_agent(
            owner_id=owner_id,
            task_id=task.task_id,
            filename="demo.pdf",
            markdown="# Title",
        )

    assert stream_count == 2


def test_paper_translation_extracts_translated_markdown_path():
    assert (
        _extract_translated_markdown_path(
            '{"translated_markdown_path":"/mnt/user-data/outputs/demo.zh.md"}'
        )
        == "/mnt/user-data/outputs/demo.zh.md"
    )
    assert (
        _extract_translated_markdown_path(
            '```json\n{"translated_markdown_path":"/mnt/user-data/outputs/demo.zh.md"}\n```'
        )
        == "/mnt/user-data/outputs/demo.zh.md"
    )
    assert (
        _extract_translated_markdown_path("已完成：/mnt/user-data/outputs/demo.zh.md")
        == "/mnt/user-data/outputs/demo.zh.md"
    )
    assert (
        _extract_translated_markdown_path(
            '已完成："/mnt/user-data/outputs/demo paper.zh.md"'
        )
        == "/mnt/user-data/outputs/demo paper.zh.md"
    )
    assert (
        _extract_translated_markdown_path(
            "已完成：</mnt/user-data/outputs/demo paper.zh.md>"
        )
        == "/mnt/user-data/outputs/demo paper.zh.md"
    )


def test_paper_translation_validates_translated_markdown_artifact_path():
    assert (
        _normalize_translated_markdown_artifact_path(
            "/mnt/user-data/outputs/demo.zh.md"
        )
        == "/mnt/user-data/outputs/demo.zh.md"
    )
    assert (
        _normalize_translated_markdown_artifact_path("mnt/user-data/outputs/demo.zh.md")
        == "/mnt/user-data/outputs/demo.zh.md"
    )
    with pytest.raises(ValueError):
        _normalize_translated_markdown_artifact_path("/mnt/user-data/uploads/source.md")
    with pytest.raises(ValueError):
        _normalize_translated_markdown_artifact_path("/mnt/user-data/outputs/demo.txt")
    with pytest.raises(ValueError):
        _normalize_translated_markdown_artifact_path(
            "/mnt/user-data/outputs/../demo.zh.md"
        )


@pytest.mark.asyncio
async def test_paper_translation_service_records_sanitized_failure(
    monkeypatch, tmp_path, caplog
):
    owner_id = str(uuid4())
    secret_marker = "private-mineru-provider-body"
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    source_pdf_path = service.source_pdf_path(owner_id=owner_id, task_id=task.task_id)
    source_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    source_pdf_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id=owner_id, task_id=task.task_id, source_pdf_path=source_pdf_path
    )

    monkeypatch.setattr(
        MineruService,
        "convert_document",
        AsyncMock(side_effect=RuntimeError(secret_marker)),
    )

    with caplog.at_level(
        logging.ERROR,
        logger="modules.creative_workshop.paper_translation_service",
    ):
        await service.run_translation_task(
            owner_id=owner_id,
            task_id=task.task_id,
            filename="demo.pdf",
            source_pdf_path=str(source_pdf_path),
        )

    failed = await service.get_task(owner_id=owner_id, task_id=task.task_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "论文转换或翻译失败，请重新上传后再试"
    assert secret_marker not in caplog.text
    assert secret_marker not in json.dumps(failed.__dict__, ensure_ascii=False)


@pytest.mark.asyncio
async def test_paper_translation_downloads_markdown_and_pdf(tmp_path, monkeypatch):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = await service._write_task_text_fenced(
        owner_id,
        task.task_id,
        "translation.zh.md",
        "# 标题\n\n中文正文。",
        redis_client=None,
        lease=None,
    )
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(md_path),
    )
    render_calls = 0

    class _FakeHTML:
        def __init__(self, *, string, base_url=".", url_fetcher=None):
            self.string = string
            self.base_url = base_url
            self.url_fetcher = url_fetcher

        def write_pdf(self):
            nonlocal render_calls
            render_calls += 1
            assert "中文正文" in self.string
            assert self.url_fetcher is not None
            return b"%PDF-1.7\nfake-rendered"

    monkeypatch.setitem(
        __import__("sys").modules, "weasyprint", SimpleNamespace(HTML=_FakeHTML)
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "markdown",
        SimpleNamespace(markdown=lambda text, **kwargs: f"<h1>{text}</h1>"),
    )
    markdown_name, markdown = await service.get_translated_markdown(
        owner_id=owner_id, task_id=task.task_id
    )
    pdf_name, pdf_bytes = await service.get_translated_pdf(
        owner_id=owner_id, task_id=task.task_id
    )
    cached_pdf_name, cached_pdf_bytes = await service.get_translated_pdf(
        owner_id=owner_id, task_id=task.task_id
    )

    assert markdown_name == "demo.zh.md"
    assert markdown == "# 标题\n\n中文正文。"
    assert pdf_name == "demo.zh.pdf"
    assert pdf_bytes.startswith(b"%PDF-1.7")
    assert cached_pdf_name == "demo.zh.pdf"
    assert cached_pdf_bytes == pdf_bytes
    assert render_calls == 1


def test_mineru_extracts_markdown_and_image_assets_from_zip():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("result/demo.md", "# Title\n\n![](images/fig.jpg)")
        zf.writestr("result/images/fig.jpg", b"image-bytes")
        zf.writestr("../unsafe.jpg", b"bad")

    zip_buffer.seek(0)
    result = MineruService._extract_markdown_result_from_zip_file(zip_buffer)

    assert result.markdown == "# Title\n\n![](images/fig.jpg)"
    assert result.markdown_path == "result/demo.md"
    assert result.assets == {"images/fig.jpg": b"image-bytes"}


@pytest.mark.asyncio
async def test_paper_translation_rewrites_assets_to_urls_for_result_preview(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = await service._write_task_text_fenced(
        owner_id,
        task.task_id,
        "translation.zh.md",
        "# 标题\n\n![](images/fig.jpg)",
        redis_client=None,
        lease=None,
    )
    await service._write_task_assets_fenced(
        owner_id,
        task.task_id,
        {"images/fig.jpg": b"image-bytes"},
        redis_client=None,
        lease=None,
    )
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(md_path),
    )

    _, preview_markdown = await service.get_translated_markdown(
        owner_id=owner_id,
        task_id=task.task_id,
        asset_url_prefix=f"/api/creative-workshop/paper-translation/tasks/{task.task_id}/assets",
    )
    _, download_markdown = await service.get_translated_markdown(
        owner_id=owner_id, task_id=task.task_id
    )

    assert (
        f"![](/api/creative-workshop/paper-translation/tasks/{task.task_id}/assets/images/fig.jpg)"
        in preview_markdown
    )
    assert "data:image" not in preview_markdown
    assert download_markdown == "# 标题\n\n![](images/fig.jpg)"


@pytest.mark.asyncio
async def test_paper_translation_rewrites_complex_image_destinations(
    tmp_path, monkeypatch
):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = await service._write_task_text_fenced(
        owner_id,
        task.task_id,
        "translation.zh.md",
        '# 标题\n\n![图](<images/fig (1).jpg> "caption")\n\n![普通](images/fig.jpg)',
        redis_client=None,
        lease=None,
    )
    await service._write_task_assets_fenced(
        owner_id,
        task.task_id,
        {
            "images/fig (1).jpg": b"complex-image",
            "images/fig.jpg": b"simple-image",
        },
        redis_client=None,
        lease=None,
    )
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(md_path),
    )

    _, preview_markdown = await service.get_translated_markdown(
        owner_id=owner_id,
        task_id=task.task_id,
        inline_assets=True,
    )
    pdf_ready = service._rewrite_markdown_asset_paths_for_local_pdf(
        owner_id=owner_id,
        task_id=task.task_id,
        markdown=md_path.read_text(encoding="utf-8"),
    )

    assert "data:image/jpeg;base64,Y29tcGxleC1pbWFnZQ==" in preview_markdown
    assert "data:image/jpeg;base64,c2ltcGxlLWltYWdl" in preview_markdown
    assert '![图](<assets/images/fig (1).jpg> "caption")' in pdf_ready
    assert "![普通](assets/images/fig.jpg)" in pdf_ready


def test_paper_translation_removes_mineru_mermaid_details_blocks():
    markdown = """# 标题

![](images/fig.jpg)

<details>
<summary>流程图</summary>

```mermaid
graph TD
    A["主机"] --> B["任务定义"]
```
</details>

图 1：架构概览。

```mermaid
graph TD
    X --> Y
```

正文。"""

    cleaned = _remove_mermaid_diagram_blocks(markdown)

    assert "![](images/fig.jpg)" in cleaned
    assert "图 1：架构概览。" in cleaned
    assert "正文。" in cleaned
    assert "graph TD" not in cleaned
    assert "<details>" not in cleaned
    assert "```mermaid" not in cleaned


def test_paper_translation_pdf_export_blocks_remote_resources():
    with pytest.raises(RuntimeError, match="Remote resources"):
        _safe_pdf_url_fetcher("https://example.com/figure.png")


def test_paper_translation_pdf_export_requires_scoped_raster_assets(tmp_path):
    asset_root = tmp_path / "task"
    asset_root.mkdir()
    image = asset_root / "figure.png"
    image.write_bytes(b"fake-png")
    text_file = asset_root / "task.json"
    text_file.write_text("private manifest", encoding="utf-8")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")

    assert _normalize_scoped_pdf_resource_url(
        image.as_uri(), allowed_root=asset_root
    ) == image.as_uri()

    with pytest.raises(RuntimeError, match="task asset boundary"):
        _normalize_scoped_pdf_resource_url(
            text_file.as_uri(), allowed_root=asset_root
        )
    with pytest.raises(RuntimeError, match="task asset boundary"):
        _normalize_scoped_pdf_resource_url(outside.as_uri(), allowed_root=asset_root)
    with pytest.raises(RuntimeError, match="explicit task root"):
        _normalize_scoped_pdf_resource_url(image.as_uri(), allowed_root=None)


def test_paper_translation_pdf_export_rejects_symlink_escape(tmp_path):
    asset_root = tmp_path / "task"
    asset_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    escaped = asset_root / "figure.png"
    escaped.symlink_to(outside)

    with pytest.raises(RuntimeError, match="task asset boundary"):
        _normalize_scoped_pdf_resource_url(escaped.as_uri(), allowed_root=asset_root)


@pytest.mark.asyncio
async def test_paper_translation_downloads_markdown_from_relative_task_path(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root="storage")
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = await service._write_task_text_fenced(
        owner_id,
        task.task_id,
        "translation.zh.md",
        "# 标题",
        redis_client=None,
        lease=None,
    )
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(md_path),
    )

    markdown_name, markdown = await service.get_translated_markdown(
        owner_id=owner_id, task_id=task.task_id
    )

    assert markdown_name == "demo.zh.md"
    assert markdown == "# 标题"


@pytest.mark.asyncio
async def test_paper_translation_rejects_result_path_outside_task_dir(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(tmp_path / "outside.md"),
    )

    with pytest.raises(FileNotFoundError):
        await service.get_translated_markdown(owner_id=owner_id, task_id=task.task_id)


@pytest.mark.asyncio
async def test_paper_translation_status_read_does_not_override_queue_owned_state(
    tmp_path,
):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    stale_time = datetime.now(timezone.utc) - timedelta(hours=3)
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="translating",
        updated_at=stale_time.isoformat(),
    )

    stale_task = await service.get_task(owner_id=owner_id, task_id=task.task_id)

    assert stale_task is not None
    assert stale_task.status == "translating"
    assert stale_task.error is None


@pytest.mark.asyncio
async def test_paper_translation_heartbeat_extends_only_current_lease(
    monkeypatch, tmp_path
):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_queue_visibility_timeout_seconds", lambda: 60.0)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    source_path = service.source_pdf_path(owner_id=owner_id, task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id=owner_id, task_id=task.task_id, source_pdf_path=source_path
    )
    redis_client = _FakeRedis()
    await service.enqueue_translation_task(
        redis_client,
        PaperTranslationQueueItem(
            owner_id=owner_id,
            task_id=task.task_id,
            filename=task.filename,
            source_pdf_path=str(source_path),
        ),
    )
    lease = await service.dequeue_translation_task(redis_client)
    assert lease is not None
    first_deadline = redis_client.sorted_sets[QUEUE_PROCESSING_KEY][task.task_id]

    assert await service.heartbeat_translation_task(redis_client, lease) is True
    assert (
        redis_client.sorted_sets[QUEUE_PROCESSING_KEY][task.task_id] >= first_deadline
    )

    wrong_lease = PaperTranslationTaskLease(item=lease.item, token="wrong-token")
    assert await service.heartbeat_translation_task(redis_client, wrong_lease) is False


@pytest.mark.asyncio
async def test_paper_translation_status_response_omits_markdown_body(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = await service._write_task_text_fenced(
        owner_id,
        task.task_id,
        "translation.zh.md",
        "# 很长的译文",
        redis_client=None,
        lease=None,
    )
    completed = await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(md_path),
    )

    payload = service.build_response_payload(completed)

    assert payload["status"] == "completed"
    assert "translated_markdown" not in payload


async def _enqueue_and_claim(
    service: PaperTranslationService,
    redis_client: _FakeRedis,
    *,
    owner_id: str,
    task_id: str,
    source_pdf_path: str,
    attempt: int = 1,
) -> PaperTranslationTaskLease:
    await service.enqueue_translation_task(
        redis_client,
        PaperTranslationQueueItem(
            owner_id=owner_id,
            task_id=task_id,
            filename="paper.pdf",
            source_pdf_path=source_pdf_path,
            attempt=attempt,
        ),
    )
    lease = await service.dequeue_translation_task(redis_client)
    assert lease is not None
    return lease


@pytest.mark.asyncio
async def test_paper_translation_queue_claims_once_across_workers(tmp_path):
    service = PaperTranslationService(storage_root=tmp_path)
    redis_client = _FakeRedis()
    await service.enqueue_translation_task(
        redis_client,
        PaperTranslationQueueItem(
            owner_id="owner-1",
            task_id="task-1",
            filename="paper.pdf",
            source_pdf_path="/tmp/source.pdf",
        ),
    )

    first, second = await asyncio.gather(
        service.dequeue_translation_task(redis_client),
        service.dequeue_translation_task(redis_client),
    )

    leases = [lease for lease in (first, second) if lease is not None]
    assert len(leases) == 1
    assert leases[0].token == redis_client.hashes[QUEUE_LEASE_TOKENS_KEY]["task-1"]
    assert leases[0].generation == int(
        redis_client.hashes[QUEUE_GENERATIONS_KEY]["task-1"]
    )
    assert redis_client.sorted_sets[QUEUE_PROCESSING_KEY]["task-1"] > 0


@pytest.mark.asyncio
async def test_paper_translation_old_lease_cannot_commit_or_transition(
    monkeypatch, tmp_path
):
    owner_id = "owner-1"
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_queue_visibility_timeout_seconds", lambda: 60.0)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id=owner_id, filename="paper.pdf")
    source_path = service.source_pdf_path(owner_id=owner_id, task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id=owner_id, task_id=task.task_id, source_pdf_path=source_path
    )
    old_lease = await _enqueue_and_claim(
        service,
        redis_client,
        owner_id=owner_id,
        task_id=task.task_id,
        source_pdf_path=str(source_path),
    )
    await service.activate_translation_lease(redis_client, old_lease)
    redis_client.sorted_sets[QUEUE_PROCESSING_KEY][task.task_id] = 0.0
    assert await service.recover_processing_queue(redis_client) == 1
    new_lease = await service.dequeue_translation_task(redis_client)
    assert new_lease is not None
    assert new_lease.token != old_lease.token
    new_service = PaperTranslationService(storage_root=tmp_path)
    await new_service.activate_translation_lease(redis_client, new_lease)

    new_result_path = await new_service._write_task_text_fenced(
        owner_id,
        task.task_id,
        "translation.zh.md",
        "new result",
        redis_client=redis_client,
        lease=new_lease,
    )
    await new_service._write_task_assets_fenced(
        owner_id,
        task.task_id,
        {"images/figure.png": b"new image"},
        redis_client=redis_client,
        lease=new_lease,
    )
    with pytest.raises(PaperTranslationLeaseLost):
        await service._write_task_text_fenced(
            owner_id,
            task.task_id,
            "translation.zh.md",
            "stale result",
            redis_client=redis_client,
            lease=old_lease,
        )
    with pytest.raises(PaperTranslationLeaseLost):
        await service._update_task(
            owner_id=owner_id,
            task_id=task.task_id,
            redis_client=redis_client,
            lease=old_lease,
            status="failed",
            error="stale worker failure",
        )
    await new_service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        redis_client=redis_client,
        lease=new_lease,
        status="completed",
        translated_markdown_path=str(new_result_path),
    )

    assert new_result_path.read_text() == "new result"
    completed = await new_service.get_task(owner_id=owner_id, task_id=task.task_id)
    assert completed is not None and completed.status == "completed"
    assert completed.error is None
    resolved_asset = new_service.resolve_asset_file(
        owner_id=owner_id,
        task_id=task.task_id,
        asset_path="images/figure.png",
    )
    assert resolved_asset is not None and resolved_asset.read_bytes() == b"new image"
    assert await service.acknowledge_translation_task(redis_client, old_lease) is False
    assert await service.requeue_processing_task(redis_client, old_lease) is False
    assert await service._cancel_claim(redis_client, old_lease) is False
    assert task.task_id in redis_client.sorted_sets[QUEUE_PROCESSING_KEY]


@pytest.mark.asyncio
async def test_paper_translation_fencing_closes_heartbeat_to_file_commit_race(
    monkeypatch, tmp_path
):
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_queue_visibility_timeout_seconds", lambda: 60.0)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    source_path = service.source_pdf_path(owner_id="owner-1", task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id="owner-1", task_id=task.task_id, source_pdf_path=source_path
    )
    old_lease = await _enqueue_and_claim(
        service,
        redis_client,
        owner_id="owner-1",
        task_id=task.task_id,
        source_pdf_path=str(source_path),
    )
    await service.activate_translation_lease(redis_client, old_lease)

    original_prepare = service._prepare_lease_commit
    heartbeat_passed = asyncio.Event()
    resume_old_worker = asyncio.Event()

    async def _pause_after_heartbeat(client, lease):
        await original_prepare(client, lease)
        if lease.token == old_lease.token and not heartbeat_passed.is_set():
            heartbeat_passed.set()
            await resume_old_worker.wait()

    monkeypatch.setattr(service, "_prepare_lease_commit", _pause_after_heartbeat)
    stale_write = asyncio.create_task(
        service._write_task_text_fenced(
            "owner-1",
            task.task_id,
            "translation.zh.md",
            "stale result",
            redis_client=redis_client,
            lease=old_lease,
        )
    )
    await asyncio.wait_for(heartbeat_passed.wait(), timeout=2)

    redis_client.sorted_sets[QUEUE_PROCESSING_KEY][task.task_id] = 0.0
    assert await service.recover_processing_queue(redis_client) == 1
    new_lease = await service.dequeue_translation_task(redis_client)
    assert new_lease is not None
    new_service = PaperTranslationService(storage_root=tmp_path)
    await new_service.activate_translation_lease(redis_client, new_lease)
    resume_old_worker.set()

    with pytest.raises(PaperTranslationLeaseLost):
        await stale_write
    stale_result_path = (
        service._task_dir("owner-1", task.task_id)
        / ".leases"
        / old_lease.token
        / "translation.zh.md"
    )
    assert not stale_result_path.exists()
    current = await service.get_task(owner_id="owner-1", task_id=task.task_id)
    assert current is not None
    assert current.active_lease_token == new_lease.token
    assert current.status == "queued"


@pytest.mark.asyncio
async def test_paper_translation_queue_transitions_are_complete_atomic_states(
    monkeypatch, tmp_path
):
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_max_retries", lambda: 2)
    monkeypatch.setattr(service, "_retry_delay_seconds", lambda attempt: 0.0)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    source_path = service.source_pdf_path(owner_id="owner-1", task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id="owner-1", task_id=task.task_id, source_pdf_path=source_path
    )

    lease = await _enqueue_and_claim(
        service,
        redis_client,
        owner_id="owner-1",
        task_id=task.task_id,
        source_pdf_path=str(source_path),
    )
    await service.activate_translation_lease(redis_client, lease)
    assert task.task_id not in redis_client.sorted_sets[QUEUE_PENDING_KEY]
    assert task.task_id in redis_client.sorted_sets[QUEUE_PROCESSING_KEY]
    assert task.task_id in redis_client.hashes[QUEUE_PAYLOADS_KEY]
    assert await service.acknowledge_translation_task(redis_client, lease) is False
    assert task.task_id in redis_client.hashes[QUEUE_PAYLOADS_KEY]

    assert await service.retry_translation_task(redis_client, lease=lease) is True
    assert task.task_id not in redis_client.sorted_sets[QUEUE_PROCESSING_KEY]
    assert task.task_id in redis_client.sorted_sets[QUEUE_SCHEDULED_KEY]
    assert source_path.exists()
    assert task.task_id in redis_client.hashes[QUEUE_PAYLOADS_KEY]

    assert await service.promote_due_scheduled_tasks(redis_client) == 1
    assert task.task_id in redis_client.sorted_sets[QUEUE_PENDING_KEY]
    assert task.task_id not in redis_client.sorted_sets[QUEUE_SCHEDULED_KEY]
    retry_lease = await service.dequeue_translation_task(redis_client)
    assert retry_lease is not None and retry_lease.item.attempt == 2
    assert await service.requeue_processing_task(redis_client, retry_lease) is True
    assert task.task_id in redis_client.sorted_sets[QUEUE_PENDING_KEY]
    assert task.task_id not in redis_client.sorted_sets[QUEUE_PROCESSING_KEY]

    final_lease = await service.dequeue_translation_task(redis_client)
    assert final_lease is not None
    await service.activate_translation_lease(redis_client, final_lease)
    assert await service._cancel_claim(redis_client, final_lease) is True
    assert all(
        task.task_id not in redis_client.sorted_sets[key]
        for key in (QUEUE_PENDING_KEY, QUEUE_PROCESSING_KEY, QUEUE_SCHEDULED_KEY)
    )
    assert task.task_id not in redis_client.hashes[QUEUE_PAYLOADS_KEY]
    assert task.task_id not in redis_client.hashes[QUEUE_ATTEMPTS_KEY]
    assert source_path.exists()


@pytest.mark.asyncio
async def test_paper_translation_expired_recovery_is_idempotent_and_increments_attempt(
    monkeypatch, tmp_path
):
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_queue_visibility_timeout_seconds", lambda: 60.0)
    redis_client = _FakeRedis()
    lease = await _enqueue_and_claim(
        service,
        redis_client,
        owner_id="owner-1",
        task_id="task-1",
        source_pdf_path="/tmp/source.pdf",
    )
    redis_client.sorted_sets[QUEUE_PROCESSING_KEY]["task-1"] = 0.0

    assert await service.recover_processing_queue(redis_client) == 1
    assert await service.recover_processing_queue(redis_client) == 0
    recovered_lease = await service.dequeue_translation_task(redis_client)

    assert recovered_lease is not None
    assert recovered_lease.item.attempt == 2
    assert recovered_lease.token != lease.token
    assert await service.heartbeat_translation_task(redis_client, lease) is False


@pytest.mark.asyncio
async def test_paper_translation_reconciles_manifest_enqueue_gap_and_duplicate(
    tmp_path,
):
    service = PaperTranslationService(storage_root=tmp_path)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    source_path = service.source_pdf_path(owner_id="owner-1", task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")

    assert await service.reconcile_queued_tasks(redis_client) == 1
    assert await service.reconcile_queued_tasks(redis_client) == 0
    lease = await service.dequeue_translation_task(redis_client)
    restored = await service.get_task(owner_id="owner-1", task_id=task.task_id)

    assert lease is not None
    assert lease.item.task_id == task.task_id
    assert lease.item.source_pdf_path == str(source_path.resolve())
    assert restored is not None and restored.source_pdf_path == str(
        source_path.resolve()
    )


@pytest.mark.asyncio
async def test_paper_translation_reconcile_cursor_reaches_tasks_beyond_batch(tmp_path):
    service = PaperTranslationService(storage_root=tmp_path)
    redis_client = _FakeRedis()
    tasks = []
    for owner_id in ("owner-a", "owner-b"):
        task = await service.create_task(owner_id=owner_id, filename="paper.pdf")
        source_path = service.source_pdf_path(owner_id=owner_id, task_id=task.task_id)
        source_path.write_bytes(b"%PDF-1.4\nfake")
        tasks.append(task)

    assert await service.reconcile_queued_tasks(redis_client, limit=1) == 1
    assert redis_client.strings[QUEUE_RECONCILE_CURSOR_KEY]
    assert await service.reconcile_queued_tasks(redis_client, limit=1) == 1

    assert {task.task_id for task in tasks} == set(
        redis_client.sorted_sets[QUEUE_PENDING_KEY]
    )


@pytest.mark.asyncio
async def test_paper_translation_foreign_terminal_manifest_is_reprocessed(
    monkeypatch, tmp_path
):
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_queue_visibility_timeout_seconds", lambda: 60.0)
    monkeypatch.setattr(service, "_max_retries", lambda: 1)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    source_path = service.source_pdf_path(owner_id="owner-1", task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id="owner-1", task_id=task.task_id, source_pdf_path=source_path
    )
    first_lease = await _enqueue_and_claim(
        service,
        redis_client,
        owner_id="owner-1",
        task_id=task.task_id,
        source_pdf_path=str(source_path),
    )
    await service.activate_translation_lease(redis_client, first_lease)
    translated_path = await service._write_task_text_fenced(
        "owner-1",
        task.task_id,
        "translation.zh.md",
        "old completed result",
        redis_client=redis_client,
        lease=first_lease,
    )
    await service._update_task(
        owner_id="owner-1",
        task_id=task.task_id,
        redis_client=redis_client,
        lease=first_lease,
        status="completed",
        translated_markdown_path=str(translated_path),
    )
    redis_client.sorted_sets[QUEUE_PROCESSING_KEY][task.task_id] = 0.0
    assert await service.recover_processing_queue(redis_client) == 1
    duplicate_lease = await service.dequeue_translation_task(redis_client)
    assert duplicate_lease is not None and duplicate_lease.token != first_lease.token
    worker = PaperTranslationQueueWorker(service=service, redis_client=redis_client)

    async def _rerun(**kwargs):
        current_lease = kwargs["lease"]
        current_path = await service._write_task_text_fenced(
            "owner-1",
            task.task_id,
            "translation.zh.md",
            "new completed result",
            redis_client=redis_client,
            lease=current_lease,
        )
        await service._update_task(
            owner_id="owner-1",
            task_id=task.task_id,
            redis_client=redis_client,
            lease=current_lease,
            status="completed",
            translated_markdown_path=str(current_path),
        )

    run_mock = AsyncMock(side_effect=_rerun)
    monkeypatch.setattr(service, "run_translation_task", run_mock)
    worker._running = True

    await worker._handle_lease(duplicate_lease, 0)
    worker._running = False
    completed = await service.get_task(owner_id="owner-1", task_id=task.task_id)

    run_mock.assert_awaited_once()
    assert completed is not None
    assert completed.terminal_lease_token == duplicate_lease.token
    assert completed.thread_id != task.thread_id
    assert completed.translated_markdown_path != str(translated_path)
    assert (
        Path(completed.translated_markdown_path).read_text() == "new completed result"
    )
    assert task.task_id not in redis_client.sorted_sets[QUEUE_PROCESSING_KEY]
    assert task.task_id not in redis_client.hashes[QUEUE_PAYLOADS_KEY]


@pytest.mark.asyncio
async def test_paper_translation_worker_shutdown_atomically_requeues_owned_lease(
    monkeypatch, tmp_path
):
    service = PaperTranslationService(storage_root=tmp_path)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    source_path = service.source_pdf_path(owner_id="owner-1", task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id="owner-1", task_id=task.task_id, source_pdf_path=source_path
    )
    await service.enqueue_translation_task(
        redis_client,
        PaperTranslationQueueItem(
            owner_id="owner-1",
            task_id=task.task_id,
            filename="paper.pdf",
            source_pdf_path=str(source_path),
        ),
    )
    started = asyncio.Event()

    async def _block(**kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "run_translation_task", _block)
    worker = PaperTranslationQueueWorker(service=service, redis_client=redis_client)
    worker._running = True
    consumer = asyncio.create_task(worker._consume_loop(0))
    worker._tasks = [consumer]
    await asyncio.wait_for(started.wait(), timeout=2)

    await worker.stop()

    assert task.task_id in redis_client.sorted_sets[QUEUE_PENDING_KEY]
    assert task.task_id not in redis_client.sorted_sets[QUEUE_PROCESSING_KEY]
    assert task.task_id not in redis_client.hashes[QUEUE_LEASE_TOKENS_KEY]


@pytest.mark.asyncio
async def test_paper_translation_worker_bounds_stale_retries(monkeypatch, tmp_path):
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_max_retries", lambda: 0)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    source_path = service.source_pdf_path(owner_id="owner-1", task_id=task.task_id)
    source_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(
        owner_id="owner-1", task_id=task.task_id, source_pdf_path=source_path
    )
    lease = await _enqueue_and_claim(
        service,
        redis_client,
        owner_id="owner-1",
        task_id=task.task_id,
        source_pdf_path=str(source_path),
    )
    redis_client.sorted_sets[QUEUE_PROCESSING_KEY][task.task_id] = 0.0
    await service.recover_processing_queue(redis_client)
    exhausted_lease = await service.dequeue_translation_task(redis_client)
    assert exhausted_lease is not None and exhausted_lease.item.attempt == 2
    worker = PaperTranslationQueueWorker(service=service, redis_client=redis_client)

    await worker._handle_lease(exhausted_lease, 0)
    failed_task = await service.get_task(owner_id="owner-1", task_id=task.task_id)

    assert await service.heartbeat_translation_task(redis_client, lease) is False
    assert failed_task is not None and failed_task.status == "failed"
    assert task.task_id not in redis_client.sorted_sets[QUEUE_PROCESSING_KEY]
    assert source_path.exists()


@pytest.mark.asyncio
async def test_paper_translation_manifest_replace_failure_preserves_previous_json(
    monkeypatch, tmp_path
):
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    manifest_path = service._task_manifest_path("owner-1", task.task_id)
    original = manifest_path.read_bytes()

    def _fail_replace(source, destination):
        raise OSError("simulated crash before atomic publish")

    monkeypatch.setattr(
        "modules.creative_workshop.paper_translation_service.os.replace", _fail_replace
    )
    with pytest.raises(OSError, match="simulated crash"):
        await service._update_task(
            owner_id="owner-1", task_id=task.task_id, status="translating"
        )

    assert manifest_path.read_bytes() == original
    assert json.loads(original)["status"] == "queued"
    assert not list(manifest_path.parent.glob(".*.tmp"))


@pytest.mark.asyncio
async def test_paper_translation_sse_parser_collects_message_tuple_and_values():
    class _Response:
        async def aiter_text(self):
            yield 'event: messages-tuple\ndata: {"type":"ai","id":"m1","content":"你好"}\n\n'
            yield 'event: values\ndata: {"messages":[{"type":"ai","id":"m2","content":"最终译文"}]}\n\n'

    events = []
    async for event in PaperTranslationService._iter_sse_events(_Response()):
        events.append(event)

    assert events[0] == (
        "messages-tuple",
        {"type": "ai", "id": "m1", "content": "你好"},
    )
    assert events[1] == (
        "values",
        {"messages": [{"type": "ai", "id": "m2", "content": "最终译文"}]},
    )


@pytest.mark.asyncio
async def test_download_paper_translation_markdown_response(monkeypatch):
    user_id = uuid4()

    class _Service:
        async def get_translated_markdown(
            self,
            *,
            owner_id,
            task_id,
            inline_assets=False,
            asset_url_prefix=None,
        ):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            assert inline_assets is True
            assert asset_url_prefix is None
            return "demo.zh.md", "# 标题"

    monkeypatch.setattr(
        controller, "_get_paper_translation_service", lambda: _Service()
    )

    response = await controller.download_paper_translation_markdown(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "text/markdown; charset=utf-8"
    assert response.body == "# 标题".encode("utf-8")
    assert (
        response.headers["content-disposition"]
        == "attachment; filename*=UTF-8''demo.zh.md"
    )


@pytest.mark.asyncio
async def test_download_paper_translation_markdown_for_knowledge_base_inlines_assets(
    monkeypatch,
):
    user_id = uuid4()

    class _Service:
        async def get_translated_markdown(
            self,
            *,
            owner_id,
            task_id,
            inline_assets=False,
            asset_url_prefix=None,
        ):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            assert inline_assets is True
            assert asset_url_prefix is None
            return "demo.zh.md", "# 标题\n\n![](images/fig.jpg)"

    monkeypatch.setattr(
        controller, "_get_paper_translation_service", lambda: _Service()
    )

    response = await controller.download_paper_translation_markdown_for_knowledge_base(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "text/markdown; charset=utf-8"
    assert response.body == "# 标题\n\n![](images/fig.jpg)".encode("utf-8")
    assert (
        response.headers["content-disposition"]
        == "attachment; filename*=UTF-8''demo.zh.md"
    )


@pytest.mark.asyncio
async def test_get_paper_translation_result_response(monkeypatch):
    user_id = uuid4()

    class _Service:
        async def get_translated_markdown(
            self,
            *,
            owner_id,
            task_id,
            inline_assets=False,
            asset_url_prefix=None,
            sign_asset_url=None,
        ):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            assert inline_assets is False
            assert (
                asset_url_prefix
                == "/api/creative-workshop/paper-translation/tasks/task-1/assets"
            )
            assert sign_asset_url is not None
            signed_url = sign_asset_url(
                "/api/creative-workshop/paper-translation/tasks/task-1/assets/images/fig.jpg",
                "images/fig.jpg",
            )
            return "demo.zh.md", f"# 标题\n\n![]({signed_url})"

    monkeypatch.setattr(
        controller, "_get_paper_translation_service", lambda: _Service()
    )

    response = await controller.get_paper_translation_result(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "text/markdown; charset=utf-8"
    body = response.body.decode("utf-8")
    assert body.startswith(
        "# 标题\n\n![](/api/creative-workshop/paper-translation/tasks/task-1/assets/images/fig.jpg?asset_token="
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_get_paper_translation_asset_response(monkeypatch, tmp_path):
    user_id = uuid4()
    asset_path = tmp_path / "fig.jpg"
    asset_path.write_bytes(b"image-bytes")

    class _Service:
        async def get_task(self, *, owner_id, task_id):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            return SimpleNamespace(status="completed")

        def resolve_asset_file(self, *, owner_id, task_id, asset_path):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            assert asset_path == "images/fig.jpg"
            return asset_path_fixture

    asset_path_fixture = asset_path
    monkeypatch.setattr(
        controller, "_get_paper_translation_service", lambda: _Service()
    )

    response = await controller.get_paper_translation_asset(
        task_id="task-1",
        asset_path="images/fig.jpg",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "image/jpeg"
    assert response.body == b"image-bytes"
    assert response.headers["cache-control"] == "private, max-age=3600"


@pytest.mark.asyncio
async def test_get_paper_translation_asset_accepts_signed_token(monkeypatch, tmp_path):
    user_id = uuid4()
    asset_path = tmp_path / "fig.jpg"
    asset_path.write_bytes(b"image-bytes")
    token = controller._create_paper_translation_asset_token(
        owner_id=str(user_id),
        task_id="task-1",
        asset_path="images/fig.jpg",
    )
    from utils.security import decode_access_token

    assert decode_access_token(token) is None

    class _Service:
        async def get_task(self, *, owner_id, task_id):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            return SimpleNamespace(status="completed")

        def resolve_asset_file(self, *, owner_id, task_id, asset_path):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            assert asset_path == "images/fig.jpg"
            return asset_path_fixture

    asset_path_fixture = asset_path
    monkeypatch.setattr(
        controller, "_get_paper_translation_service", lambda: _Service()
    )

    response = await controller.get_paper_translation_asset(
        task_id="task-1",
        asset_path="images/fig.jpg",
        asset_token=token,
        current_user=None,
    )

    assert response.media_type == "image/jpeg"
    assert response.body == b"image-bytes"


@pytest.mark.asyncio
async def test_favorite_paper_translation_result_creates_and_favorites_document(
    monkeypatch,
):
    user_id = uuid4()
    calls = {}

    class _TranslationService:
        async def get_translated_markdown(
            self, *, owner_id, task_id, inline_assets=False, **kwargs
        ):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            assert inline_assets is True
            return "demo.zh.md", "# 标题"

    class _KbRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_owner_and_name(self, owner_id, name):
            calls["lookup_kb"] = (owner_id, name)
            return None

        async def create(self, owner_id, name, description, category):
            calls["create_kb"] = (owner_id, name, description, category)
            return SimpleNamespace(id="kb-1")

    class _DocumentService:
        def __init__(self, db):
            self.db = db

        async def create_markdown_document_from_content(self, **kwargs):
            calls["create_doc"] = kwargs
            return SimpleNamespace(id="doc-1", name=kwargs["filename"])

    class _FavoriteService:
        def __init__(self, db):
            self.db = db

        async def favorite_document(self, doc_id, kb_id, owner_id):
            calls["favorite_doc"] = (doc_id, kb_id, owner_id)
            return {"success": True}

    monkeypatch.setattr(
        controller, "_get_paper_translation_service", lambda: _TranslationService()
    )
    monkeypatch.setattr(
        "modules.knowledge.repositories.kb_repository.KnowledgeBaseRepository", _KbRepo
    )
    monkeypatch.setattr(
        "modules.knowledge.services.document_service.DocumentService", _DocumentService
    )
    monkeypatch.setattr(
        "modules.favorites.services.favorite_service.FavoriteService", _FavoriteService
    )

    response = await controller.favorite_paper_translation_result(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
        db=SimpleNamespace(),
    )

    assert response.success is True
    assert response.kb_id == "kb-1"
    assert response.document_id == "doc-1"
    assert calls["lookup_kb"] == (str(user_id), "我的知识库")
    assert calls["create_doc"]["source"] == "creative_workshop_paper_translation:task-1"
    assert calls["create_doc"]["markdown"] == "# 标题"
    assert calls["favorite_doc"] == ("doc-1", "kb-1", str(user_id))


@pytest.mark.asyncio
async def test_get_paper_translation_favorite_status_returns_existing_state(
    monkeypatch,
):
    user_id = uuid4()

    class _KbRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_owner_and_name(self, owner_id, name):
            assert owner_id == str(user_id)
            assert name == "我的知识库"
            return SimpleNamespace(id="kb-1")

    class _DocumentRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_kb_and_source(self, kb_id, source):
            assert kb_id == "kb-1"
            assert source == "creative_workshop_paper_translation:task-1"
            return SimpleNamespace(id="doc-1", name="demo.zh.md")

    class _FavoriteService:
        def __init__(self, db):
            self.db = db

        async def check_favorites(self, owner_id, items):
            assert owner_id == str(user_id)
            assert items == [{"type": "document", "id": "doc-1"}]
            return {"document:doc-1": True}

    monkeypatch.setattr(
        "modules.knowledge.repositories.kb_repository.KnowledgeBaseRepository", _KbRepo
    )
    monkeypatch.setattr(
        "modules.knowledge.repositories.document_repository.DocumentRepository",
        _DocumentRepo,
    )
    monkeypatch.setattr(
        "modules.favorites.services.favorite_service.FavoriteService", _FavoriteService
    )

    response = await controller.get_paper_translation_favorite_status(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
        db=SimpleNamespace(),
    )

    assert response.favorited is True
    assert response.kb_id == "kb-1"
    assert response.document_id == "doc-1"
    assert response.document_name == "demo.zh.md"


@pytest.mark.asyncio
async def test_get_paper_translation_favorite_status_returns_false_when_document_missing(
    monkeypatch,
):
    user_id = uuid4()

    class _KbRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_owner_and_name(self, owner_id, name):
            return SimpleNamespace(id="kb-1")

    class _DocumentRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_kb_and_source(self, kb_id, source):
            return None

    monkeypatch.setattr(
        "modules.knowledge.repositories.kb_repository.KnowledgeBaseRepository", _KbRepo
    )
    monkeypatch.setattr(
        "modules.knowledge.repositories.document_repository.DocumentRepository",
        _DocumentRepo,
    )

    response = await controller.get_paper_translation_favorite_status(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
        db=SimpleNamespace(),
    )

    assert response.favorited is False
    assert response.kb_id == "kb-1"
    assert response.document_id is None


@pytest.mark.asyncio
async def test_get_paper_translation_source_pdf_response(monkeypatch):
    user_id = uuid4()

    class _Service:
        async def get_source_pdf(self, *, owner_id, task_id):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            return "demo.pdf", b"%PDF-1.4\nfake"

    monkeypatch.setattr(
        controller, "_get_paper_translation_service", lambda: _Service()
    )

    response = await controller.get_paper_translation_source_pdf(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.4\nfake"
    assert (
        response.headers["content-disposition"] == "inline; filename*=UTF-8''demo.pdf"
    )
