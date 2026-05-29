import os
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

os.environ["DEBUG"] = "false"

import pytest
from fastapi import HTTPException

from modules.creative_workshop import controller
from modules.creative_workshop.paper_translation_service import (
    QUEUE_PENDING_KEY,
    QUEUE_PROCESSING_KEY,
    QUEUE_PROCESSING_HEARTBEAT_KEY,
    QUEUE_SCHEDULED_KEY,
    PaperTranslationQueueItem,
    PaperTranslationQueueWorker,
    PaperTranslationService,
    PaperTranslationTask,
    _extract_translated_markdown_path,
    _normalize_translated_markdown_artifact_path,
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
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_API_KEY", "test-key")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_MODEL", "gpt-image-2")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_TIMEOUT", 12.0)

    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": "ZmFrZS1pbWFnZQ=="}]}

    class _Client:
        def __init__(self, *args, **kwargs):
            calls.append(("init", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            calls.append(("post", url, headers, json))
            return _Response()

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
    assert calls[0] == ("init", {"timeout": 12.0})
    assert calls[1][1] == "https://example.test/v1/images/generations"
    assert calls[1][2]["Authorization"] == "Bearer test-key"
    assert calls[1][3] == {
        "model": "gpt-image-2",
        "prompt": "minimal icon",
        "size": "1536x1024",
        "quality": "medium",
        "output_format": "jpeg",
        "output_compression": 80,
    }
    controller.record_user_prompt_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_image_records_image_prompt(monkeypatch, tmp_path):
    user_id = uuid4()
    monkeypatch.setattr(controller.settings, "AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_API_KEY", "test-key")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_MODEL", "gpt-image-2")

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": "ZmFrZS1pbWFnZQ=="}]}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            return _Response()

    monkeypatch.setattr(controller.httpx, "AsyncClient", _Client)

    await controller.generate_image(
        request=controller.ImageGenerationRequest(
            prompt="赛博城市夜景",
            size="1024x1536",
            quality="high",
            output_format="png",
            output_compression=None,
        ),
        current_user=SimpleNamespace(id=user_id, name="alice", email="alice@example.com"),
    )

    [log_file] = list(tmp_path.glob("*/user-*.jsonl"))
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["event_type"] == "image2_prompt"
    assert record["user"]["id"] == str(user_id)
    assert record["prompt"] == "赛博城市夜景"
    assert record["metadata"]["model"] == "gpt-image-2"
    assert record["metadata"]["size"] == "1024x1536"


class _FakeUpload:
    def __init__(self, *, filename: str, content: bytes, content_type: str = "application/pdf"):
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
        return [member for member, score in values.items() if minimum <= score <= maximum]

    async def zrem(self, key: str, member: str):
        return int(self.sorted_sets.setdefault(key, {}).pop(member, None) is not None)

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
                        results.append(await redis_client.zadd(operation[1], operation[2]))
                    elif operation[0] == "lrem":
                        results.append(await redis_client.lrem(operation[1], operation[2], operation[3]))
                    elif operation[0] == "hdel":
                        results.append(await redis_client.hdel(operation[1], operation[2]))
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
async def test_save_pdf_upload_rejects_oversized_file_before_full_read(monkeypatch, tmp_path):
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
    monkeypatch.setattr(controller, "get_redis_client", AsyncMock(return_value=redis_client))
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    response = await controller.create_paper_translation_task(
        file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
        current_user=SimpleNamespace(id=user_id, name="alice", email="alice@example.com"),
    )

    assert response.task_id == "task-1"
    assert response.status == "queued"
    service.create_task.assert_awaited_once_with(owner_id=str(user_id), filename="paper.pdf", model_name=None)
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
async def test_create_paper_translation_task_preserves_selected_model(monkeypatch, tmp_path):
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
    monkeypatch.setattr(controller, "get_redis_client", AsyncMock(return_value=redis_client))
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    response = await controller.create_paper_translation_task(
        file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
        model_name="  model-custom  ",
        current_user=SimpleNamespace(id=user_id, name="alice", email="alice@example.com"),
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
async def test_create_paper_translation_task_rejects_invalid_pdf_before_task_creation(monkeypatch, tmp_path):
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
            current_user=SimpleNamespace(id=user_id, name="alice", email="alice@example.com"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "INVALID_PDF"
    service.create_task.assert_not_awaited()
    controller.record_user_prompt_event.assert_not_awaited()
    assert not list((tmp_path / "storage" / "_incoming").glob("*.pdf"))


@pytest.mark.asyncio
async def test_create_paper_translation_task_reports_unwritable_storage(monkeypatch, tmp_path):
    user_id = uuid4()

    class _Service:
        storage_root = tmp_path / "storage"
        create_task = AsyncMock()

    service = _Service()
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: service)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only")))

    with pytest.raises(HTTPException) as exc_info:
        await controller.create_paper_translation_task(
            file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
            current_user=SimpleNamespace(id=user_id, name="alice", email="alice@example.com"),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "STORAGE_UNAVAILABLE"
    service.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_paper_translation_task_marks_failed_when_queue_unavailable(monkeypatch, tmp_path):
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
    monkeypatch.setattr(controller, "get_redis_client", AsyncMock(return_value=_FakeRedis()))
    monkeypatch.setattr(controller, "record_user_prompt_event", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await controller.create_paper_translation_task(
            file=_FakeUpload(filename="paper.pdf", content=b"%PDF-1.4\nfake"),
            current_user=SimpleNamespace(id=user_id, name="alice", email="alice@example.com"),
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
    await service.attach_source_pdf(owner_id=owner_id, task_id=task.task_id, source_pdf_path=source_pdf_path)

    monkeypatch.setattr(MineruService, "convert_document", AsyncMock(return_value={"batch_id": "batch-1"}))
    monkeypatch.setattr(MineruService, "get_task_status", AsyncMock(return_value={"status": "completed"}))
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
        AsyncMock(return_value="# 标题\n\n中文正文。\n\n![](images/fig.jpg)\n\n## References\nSmith, 2020."),
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
    assert "English text" in open(completed.source_markdown_path, encoding="utf-8").read()
    assert "中文正文" in open(completed.translated_markdown_path, encoding="utf-8").read()
    assert (tmp_path / owner_id / task.task_id / "assets" / "images" / "fig.jpg").read_bytes() == b"fake-image"
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
async def test_paper_translation_agent_uses_skill_and_downloads_artifact(monkeypatch, tmp_path):
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
                    "disable_model_streaming": kwargs.get("disable_model_streaming", False),
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
                'event: values\n'
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
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf", model_name="model-custom")

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
    assert captured["stream_request"]["context"]["disable_model_streaming"] is False
    assert captured["stream_request"]["context"]["dynamic_model_token"] == "token-1"
    assert "paper-translation" in captured["stream_request"]["input"]["messages"][0]["content"]
    assert captured["downloaded_artifact"] == (task.thread_id, "/mnt/user-data/outputs/demo.zh.md")


def test_paper_translation_extracts_translated_markdown_path():
    assert _extract_translated_markdown_path(
        '{"translated_markdown_path":"/mnt/user-data/outputs/demo.zh.md"}'
    ) == "/mnt/user-data/outputs/demo.zh.md"
    assert _extract_translated_markdown_path(
        '```json\n{"translated_markdown_path":"/mnt/user-data/outputs/demo.zh.md"}\n```'
    ) == "/mnt/user-data/outputs/demo.zh.md"
    assert _extract_translated_markdown_path(
        "已完成：/mnt/user-data/outputs/demo.zh.md"
    ) == "/mnt/user-data/outputs/demo.zh.md"
    assert _extract_translated_markdown_path(
        '已完成："/mnt/user-data/outputs/demo paper.zh.md"'
    ) == "/mnt/user-data/outputs/demo paper.zh.md"
    assert _extract_translated_markdown_path(
        "已完成：</mnt/user-data/outputs/demo paper.zh.md>"
    ) == "/mnt/user-data/outputs/demo paper.zh.md"


def test_paper_translation_validates_translated_markdown_artifact_path():
    assert (
        _normalize_translated_markdown_artifact_path("/mnt/user-data/outputs/demo.zh.md")
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
        _normalize_translated_markdown_artifact_path("/mnt/user-data/outputs/../demo.zh.md")


@pytest.mark.asyncio
async def test_paper_translation_service_records_failure(monkeypatch, tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    source_pdf_path = service.source_pdf_path(owner_id=owner_id, task_id=task.task_id)
    source_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    source_pdf_path.write_bytes(b"%PDF-1.4\nfake")
    await service.attach_source_pdf(owner_id=owner_id, task_id=task.task_id, source_pdf_path=source_pdf_path)

    monkeypatch.setattr(MineruService, "convert_document", AsyncMock(side_effect=RuntimeError("mineru down")))

    await service.run_translation_task(
        owner_id=owner_id,
        task_id=task.task_id,
        filename="demo.pdf",
        source_pdf_path=str(source_pdf_path),
    )

    failed = await service.get_task(owner_id=owner_id, task_id=task.task_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "mineru down"


@pytest.mark.asyncio
async def test_paper_translation_downloads_markdown_and_pdf(tmp_path, monkeypatch):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = service._write_task_text(owner_id, task.task_id, "translation.zh.md", "# 标题\n\n中文正文。")
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

    monkeypatch.setitem(__import__("sys").modules, "weasyprint", SimpleNamespace(HTML=_FakeHTML))
    monkeypatch.setitem(
        __import__("sys").modules,
        "markdown",
        SimpleNamespace(markdown=lambda text, **kwargs: f"<h1>{text}</h1>"),
    )
    markdown_name, markdown = await service.get_translated_markdown(owner_id=owner_id, task_id=task.task_id)
    pdf_name, pdf_bytes = await service.get_translated_pdf(owner_id=owner_id, task_id=task.task_id)
    cached_pdf_name, cached_pdf_bytes = await service.get_translated_pdf(owner_id=owner_id, task_id=task.task_id)

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

    result = MineruService._extract_markdown_result_from_zip(zip_buffer.getvalue())

    assert result.markdown == "# Title\n\n![](images/fig.jpg)"
    assert result.markdown_path == "result/demo.md"
    assert result.assets == {"images/fig.jpg": b"image-bytes"}


@pytest.mark.asyncio
async def test_paper_translation_rewrites_assets_to_urls_for_result_preview(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = service._write_task_text(owner_id, task.task_id, "translation.zh.md", "# 标题\n\n![](images/fig.jpg)")
    service._write_task_assets(owner_id, task.task_id, {"images/fig.jpg": b"image-bytes"})
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
    _, download_markdown = await service.get_translated_markdown(owner_id=owner_id, task_id=task.task_id)

    assert f"![](/api/creative-workshop/paper-translation/tasks/{task.task_id}/assets/images/fig.jpg)" in preview_markdown
    assert "data:image" not in preview_markdown
    assert download_markdown == "# 标题\n\n![](images/fig.jpg)"


@pytest.mark.asyncio
async def test_paper_translation_rewrites_complex_image_destinations(tmp_path, monkeypatch):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = service._write_task_text(
        owner_id,
        task.task_id,
        "translation.zh.md",
        '# 标题\n\n![图](<images/fig (1).jpg> "caption")\n\n![普通](images/fig.jpg)',
    )
    service._write_task_assets(
        owner_id,
        task.task_id,
        {
            "images/fig (1).jpg": b"complex-image",
            "images/fig.jpg": b"simple-image",
        },
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


@pytest.mark.asyncio
async def test_paper_translation_downloads_markdown_from_relative_task_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root="storage")
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = service._write_task_text(owner_id, task.task_id, "translation.zh.md", "# 标题")
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(md_path),
    )

    markdown_name, markdown = await service.get_translated_markdown(owner_id=owner_id, task_id=task.task_id)

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
async def test_paper_translation_marks_stale_running_task_failed(monkeypatch, tmp_path):
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
    monkeypatch.setattr(service, "_stale_after_seconds", lambda: 1.0)

    stale_task = await service.get_task(owner_id=owner_id, task_id=task.task_id)

    assert stale_task is not None
    assert stale_task.status == "failed"
    assert stale_task.error == "论文翻译任务已中断，请重新上传后再试"


@pytest.mark.asyncio
async def test_paper_translation_does_not_mark_queued_task_stale(monkeypatch, tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    stale_time = datetime.now(timezone.utc) - timedelta(hours=3)
    await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="queued",
        updated_at=stale_time.isoformat(),
    )
    monkeypatch.setattr(service, "_stale_after_seconds", lambda: 1.0)

    queued_task = await service.get_task(owner_id=owner_id, task_id=task.task_id)

    assert queued_task is not None
    assert queued_task.status == "queued"
    assert queued_task.error is None


@pytest.mark.asyncio
async def test_paper_translation_heartbeat_refreshes_running_task_updated_at(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    stale_time = datetime.now(timezone.utc) - timedelta(hours=3)
    running = await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="translating",
        updated_at=stale_time.isoformat(),
    )
    redis_client = _FakeRedis()
    raw_item = json.dumps(
        PaperTranslationQueueItem(
            owner_id=owner_id,
            task_id=task.task_id,
            filename=running.filename,
            source_pdf_path="/tmp/source.pdf",
        ).__dict__
    )

    await service.heartbeat_translation_task(redis_client, raw_item)
    refreshed = await service.get_task(owner_id=owner_id, task_id=task.task_id)

    assert refreshed is not None
    assert refreshed.status == "translating"
    assert refreshed.updated_at != stale_time.isoformat()


@pytest.mark.asyncio
async def test_paper_translation_status_response_omits_markdown_body(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="demo.pdf")
    md_path = service._write_task_text(owner_id, task.task_id, "translation.zh.md", "# 很长的译文")
    completed = await service._update_task(
        owner_id=owner_id,
        task_id=task.task_id,
        status="completed",
        translated_markdown_path=str(md_path),
    )

    payload = service.build_response_payload(completed)

    assert payload["status"] == "completed"
    assert "translated_markdown" not in payload


@pytest.mark.asyncio
async def test_paper_translation_get_latest_active_task(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    older = await service.create_task(owner_id=owner_id, filename="older.pdf")
    newer = await service.create_task(owner_id=owner_id, filename="newer.pdf")
    await service._update_task(owner_id=owner_id, task_id=older.task_id, status="translating")
    await service._update_task(owner_id=owner_id, task_id=newer.task_id, status="converting")

    latest = await service.get_latest_active_task(owner_id=owner_id)

    assert latest is not None
    assert latest.task_id == newer.task_id


@pytest.mark.asyncio
async def test_paper_translation_get_latest_active_task_ignores_completed(tmp_path):
    owner_id = str(uuid4())
    service = PaperTranslationService(storage_root=tmp_path)
    task = await service.create_task(owner_id=owner_id, filename="done.pdf")
    await service._update_task(owner_id=owner_id, task_id=task.task_id, status="completed")

    assert await service.get_latest_active_task(owner_id=owner_id) is None


@pytest.mark.asyncio
async def test_paper_translation_queue_recovers_stale_processing_items(monkeypatch):
    service = PaperTranslationService(storage_root="/tmp/paper-translation-test")
    monkeypatch.setattr(service, "_queue_visibility_timeout_seconds", lambda: 10.0)
    monkeypatch.setattr(service, "_max_retries", lambda: 1)
    redis_client = _FakeRedis()
    stale_item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id="stale-task",
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
    )
    active_item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id="active-task",
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
    )
    stale_raw = json.dumps(stale_item.__dict__)
    active_raw = json.dumps(active_item.__dict__)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, stale_raw)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, active_raw)
    await redis_client.hset(QUEUE_PROCESSING_HEARTBEAT_KEY, stale_raw, "1")
    await redis_client.hset(QUEUE_PROCESSING_HEARTBEAT_KEY, active_raw, str(datetime.now(timezone.utc).timestamp()))

    recovered = await service.recover_processing_queue(redis_client)
    dequeued = await service.dequeue_translation_task(redis_client)

    assert recovered == 1
    assert dequeued is not None
    raw_item, parsed = dequeued
    assert json.loads(raw_item)["task_id"] == "stale-task"
    assert parsed.task_id == "stale-task"
    assert active_raw in redis_client.lists[QUEUE_PROCESSING_KEY]


@pytest.mark.asyncio
async def test_paper_translation_queue_defers_untracked_processing_items(monkeypatch):
    service = PaperTranslationService(storage_root="/tmp/paper-translation-test")
    redis_client = _FakeRedis()
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id="recent-task",
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, raw_item)

    recovered = await service.recover_processing_queue(redis_client)

    assert recovered == 0
    assert raw_item in redis_client.lists[QUEUE_PROCESSING_KEY]
    assert redis_client.hashes[QUEUE_PROCESSING_HEARTBEAT_KEY][raw_item]


@pytest.mark.asyncio
async def test_paper_translation_queue_marks_stale_processing_failed_when_retries_disabled(monkeypatch, tmp_path):
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_queue_visibility_timeout_seconds", lambda: 10.0)
    monkeypatch.setattr(service, "_max_retries", lambda: 0)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    await service._update_task(owner_id="owner-1", task_id=task.task_id, status="translating")
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id=task.task_id,
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, raw_item)
    await redis_client.hset(QUEUE_PROCESSING_HEARTBEAT_KEY, raw_item, "1")

    recovered = await service.recover_processing_queue(redis_client)
    failed_task = await service.get_task(owner_id="owner-1", task_id=task.task_id)

    assert recovered == 1
    assert raw_item not in redis_client.lists[QUEUE_PROCESSING_KEY]
    assert redis_client.lists.get(QUEUE_PENDING_KEY) is None
    assert failed_task is not None
    assert failed_task.status == "failed"
    assert failed_task.error == "论文翻译任务已中断，请重新上传后再试"


@pytest.mark.asyncio
async def test_paper_translation_queue_requeues_processing_item_on_shutdown():
    service = PaperTranslationService(storage_root="/tmp/paper-translation-test")
    redis_client = _FakeRedis()
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id="task-1",
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, raw_item)
    await service.heartbeat_translation_task(redis_client, raw_item)

    requeued = await service.requeue_processing_task(redis_client, raw_item)

    assert requeued is True
    assert raw_item not in redis_client.lists[QUEUE_PROCESSING_KEY]
    assert raw_item not in redis_client.hashes[QUEUE_PROCESSING_HEARTBEAT_KEY]
    assert raw_item in redis_client.lists[QUEUE_PENDING_KEY]
    dequeued = await service.dequeue_translation_task(redis_client)
    assert dequeued is not None
    _, parsed = dequeued
    assert parsed.task_id == "task-1"


@pytest.mark.asyncio
async def test_paper_translation_queue_retries_failed_item(monkeypatch):
    service = PaperTranslationService(storage_root="/tmp/paper-translation-test")
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id=task.task_id,
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
        attempt=1,
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, raw_item)
    monkeypatch.setattr(service, "_max_retries", lambda: 2)
    monkeypatch.setattr(service, "_retry_delay_seconds", lambda attempt: 0.0)

    retried = await service.retry_translation_task(redis_client, raw_item=raw_item, item=item)

    assert retried is True
    assert raw_item not in redis_client.lists[QUEUE_PROCESSING_KEY]
    assert redis_client.lists.get(QUEUE_PENDING_KEY) is None
    [scheduled] = list(redis_client.sorted_sets[QUEUE_SCHEDULED_KEY])
    payload = json.loads(scheduled)
    assert payload["task_id"] == task.task_id
    assert payload["attempt"] == 2
    assert payload["model_name"] is None
    retried_task = await service.get_task(owner_id="owner-1", task_id=task.task_id)
    assert retried_task is not None
    assert retried_task.status == "queued"
    assert retried_task.error is None


@pytest.mark.asyncio
async def test_paper_translation_queue_promotes_due_retry(monkeypatch):
    service = PaperTranslationService(storage_root="/tmp/paper-translation-test")
    redis_client = _FakeRedis()
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id="task-1",
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
        attempt=2,
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.zadd(QUEUE_SCHEDULED_KEY, {raw_item: 1.0})

    promoted = await service.promote_due_scheduled_tasks(redis_client)

    assert promoted == 1
    assert raw_item in redis_client.lists[QUEUE_PENDING_KEY]
    assert raw_item not in redis_client.sorted_sets[QUEUE_SCHEDULED_KEY]


@pytest.mark.asyncio
async def test_paper_translation_queue_does_not_retry_after_max_retries(monkeypatch):
    service = PaperTranslationService(storage_root="/tmp/paper-translation-test")
    redis_client = _FakeRedis()
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id="task-1",
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
        attempt=3,
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, raw_item)
    monkeypatch.setattr(service, "_max_retries", lambda: 2)

    retried = await service.retry_translation_task(redis_client, raw_item=raw_item, item=item)

    assert retried is False
    assert raw_item not in redis_client.lists[QUEUE_PROCESSING_KEY]
    assert redis_client.lists.get(QUEUE_PENDING_KEY) is None
    assert redis_client.sorted_sets.get(QUEUE_SCHEDULED_KEY) is None


@pytest.mark.asyncio
async def test_paper_translation_queue_does_not_retry_when_retries_disabled(monkeypatch):
    service = PaperTranslationService(storage_root="/tmp/paper-translation-test")
    redis_client = _FakeRedis()
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id="task-1",
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
        attempt=1,
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.lpush(QUEUE_PROCESSING_KEY, raw_item)
    monkeypatch.setattr(service, "_max_retries", lambda: 0)

    retried = await service.retry_translation_task(redis_client, raw_item=raw_item, item=item)

    assert retried is False
    assert raw_item not in redis_client.lists[QUEUE_PROCESSING_KEY]
    assert redis_client.lists.get(QUEUE_PENDING_KEY) is None
    assert redis_client.sorted_sets.get(QUEUE_SCHEDULED_KEY) is None


@pytest.mark.asyncio
async def test_paper_translation_worker_records_original_failure_when_retries_disabled(monkeypatch, tmp_path):
    service = PaperTranslationService(storage_root=tmp_path)
    monkeypatch.setattr(service, "_max_retries", lambda: 0)
    redis_client = _FakeRedis()
    task = await service.create_task(owner_id="owner-1", filename="paper.pdf")
    item = PaperTranslationQueueItem(
        owner_id="owner-1",
        task_id=task.task_id,
        filename="paper.pdf",
        source_pdf_path="/tmp/source.pdf",
    )
    raw_item = json.dumps(item.__dict__)
    await redis_client.lpush(QUEUE_PENDING_KEY, raw_item)

    async def _fail_once(**kwargs):
        worker._running = False
        raise RuntimeError("agent stream failed")

    monkeypatch.setattr(service, "run_translation_task", _fail_once)
    monkeypatch.setattr("modules.creative_workshop.paper_translation_service.asyncio.sleep", AsyncMock())
    worker = PaperTranslationQueueWorker(
        service=service,
        redis_client=redis_client,
    )
    worker._running = True

    await worker._consume_loop(0)
    failed_task = await service.get_task(owner_id="owner-1", task_id=task.task_id)

    assert raw_item not in redis_client.lists[QUEUE_PROCESSING_KEY]
    assert failed_task is not None
    assert failed_task.status == "failed"
    assert failed_task.error == "agent stream failed"


@pytest.mark.asyncio
async def test_paper_translation_sse_parser_collects_message_tuple_and_values():
    class _Response:
        async def aiter_text(self):
            yield 'event: messages-tuple\ndata: {"type":"ai","id":"m1","content":"你好"}\n\n'
            yield 'event: values\ndata: {"messages":[{"type":"ai","id":"m2","content":"最终译文"}]}\n\n'

    events = []
    async for event in PaperTranslationService._iter_sse_events(_Response()):
        events.append(event)

    assert events[0] == ("messages-tuple", {"type": "ai", "id": "m1", "content": "你好"})
    assert events[1] == ("values", {"messages": [{"type": "ai", "id": "m2", "content": "最终译文"}]})


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

    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: _Service())

    response = await controller.download_paper_translation_markdown(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "text/markdown; charset=utf-8"
    assert response.body == "# 标题".encode("utf-8")
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''demo.zh.md"


@pytest.mark.asyncio
async def test_download_paper_translation_markdown_for_knowledge_base_uses_plain_markdown(monkeypatch):
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
            assert inline_assets is False
            assert asset_url_prefix is None
            return "demo.zh.md", "# 标题\n\n![](images/fig.jpg)"

    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: _Service())

    response = await controller.download_paper_translation_markdown_for_knowledge_base(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "text/markdown; charset=utf-8"
    assert response.body == "# 标题\n\n![](images/fig.jpg)".encode("utf-8")
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''demo.zh.md"


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
            assert asset_url_prefix == "/api/creative-workshop/paper-translation/tasks/task-1/assets"
            assert sign_asset_url is not None
            signed_url = sign_asset_url(
                "/api/creative-workshop/paper-translation/tasks/task-1/assets/images/fig.jpg",
                "images/fig.jpg",
            )
            return "demo.zh.md", f"# 标题\n\n![]({signed_url})"

    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: _Service())

    response = await controller.get_paper_translation_result(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "text/markdown; charset=utf-8"
    body = response.body.decode("utf-8")
    assert body.startswith("# 标题\n\n![](/api/creative-workshop/paper-translation/tasks/task-1/assets/images/fig.jpg?asset_token=")
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
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: _Service())

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
    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: _Service())

    response = await controller.get_paper_translation_asset(
        task_id="task-1",
        asset_path="images/fig.jpg",
        asset_token=token,
        current_user=None,
    )

    assert response.media_type == "image/jpeg"
    assert response.body == b"image-bytes"


@pytest.mark.asyncio
async def test_get_paper_translation_source_pdf_response(monkeypatch):
    user_id = uuid4()

    class _Service:
        async def get_source_pdf(self, *, owner_id, task_id):
            assert owner_id == str(user_id)
            assert task_id == "task-1"
            return "demo.pdf", b"%PDF-1.4\nfake"

    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: _Service())

    response = await controller.get_paper_translation_source_pdf(
        task_id="task-1",
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.media_type == "application/pdf"
    assert response.body == b"%PDF-1.4\nfake"
    assert response.headers["content-disposition"] == "inline; filename*=UTF-8''demo.pdf"


@pytest.mark.asyncio
async def test_get_latest_active_paper_translation_task_response(monkeypatch):
    user_id = uuid4()
    task = PaperTranslationTask(
        task_id="task-1",
        owner_id=str(user_id),
        filename="demo.pdf",
        thread_id="paper-translation-task-1",
        status="translating",
        created_at="2026-05-29T00:00:00+00:00",
        updated_at="2026-05-29T00:01:00+00:00",
    )

    class _Service:
        async def get_latest_active_task(self, *, owner_id):
            assert owner_id == str(user_id)
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

    monkeypatch.setattr(controller, "_get_paper_translation_service", lambda: _Service())

    response = await controller.get_latest_active_paper_translation_task(
        current_user=SimpleNamespace(id=user_id),
    )

    assert response.task_id == "task-1"
    assert response.status == "translating"
