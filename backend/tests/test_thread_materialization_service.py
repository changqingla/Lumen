import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.chat import runtime_controller
from modules.chat.services.thread_materialization_service import (
    ThreadMaterializationService,
)


@asynccontextmanager
async def _test_thread_guard(_thread_id: str):
    yield


def _service(runtime_service) -> ThreadMaterializationService:
    return ThreadMaterializationService(
        runtime_service=runtime_service,
        thread_guard_factory=_test_thread_guard,
    )


def _managed_record(*, kb_id: str, doc_id: str, content: str, filename: str) -> dict:
    return {
        "kb_id": kb_id,
        "doc_id": doc_id,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "thread_filename": filename,
    }


@pytest.mark.asyncio
async def test_knowledge_materialization_replaces_changed_content_and_removes_stale():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    service = _service(SimpleNamespace())
    old_digest = hashlib.sha256(b"old").hexdigest()
    old_filename = service._build_kb_target_filename(
        kb_id=kb_id,
        doc_id=doc_id,
        name="paper.md",
        content_sha256=old_digest,
    )

    runtime = SimpleNamespace(
        list_thread_uploads=AsyncMock(
            return_value=[
                {"filename": old_filename, "size": 3},
                {"filename": "kb__user-not-managed.md", "size": 4},
            ]
        ),
        upload_bytes=AsyncMock(),
        delete_thread_upload=AsyncMock(return_value={"success": True}),
    )

    async def upload_bytes(**kwargs):
        return {
            "filename": kwargs["filename"],
            "size": len(kwargs["data"]),
            "virtual_path": f"/mnt/user-data/uploads/{kwargs['filename']}",
        }

    runtime.upload_bytes.side_effect = upload_bytes
    service.runtime_service = runtime

    result = await service.sync_knowledge_documents(
        thread_id="thread-1",
        knowledge_documents=[
            {
                "kb_id": kb_id,
                "doc_id": doc_id,
                "name": "paper.md",
                "content": "new",
                "document_revision": "2026-07-15T00:00:00+00:00",
            }
        ],
        previous_materialized=[
            _managed_record(
                kb_id=kb_id,
                doc_id=doc_id,
                content="old",
                filename=old_filename,
            )
        ],
    )

    assert len(result) == 1
    assert result[0]["content_sha256"] == hashlib.sha256(b"new").hexdigest()
    assert result[0]["thread_filename"] != old_filename
    runtime.delete_thread_upload.assert_awaited_once_with(
        thread_id="thread-1",
        filename=old_filename,
    )
    assert all(
        call.kwargs["filename"] != "kb__user-not-managed.md"
        for call in runtime.delete_thread_upload.await_args_list
    )


@pytest.mark.asyncio
async def test_knowledge_materialization_reuses_manifest_version_without_upload():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    content = "same content"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    service = _service(SimpleNamespace())
    filename = service._build_kb_target_filename(
        kb_id=kb_id,
        doc_id=doc_id,
        name="paper.md",
        content_sha256=digest,
    )
    runtime = SimpleNamespace(
        list_thread_uploads=AsyncMock(
            return_value=[{"filename": filename, "size": len(content)}]
        ),
        get_thread_upload_integrity=AsyncMock(
            return_value={
                "filename": filename,
                "size": len(content.encode("utf-8")),
                "sha256": digest,
            }
        ),
        upload_bytes=AsyncMock(),
        delete_thread_upload=AsyncMock(),
    )
    service.runtime_service = runtime

    result = await service.sync_knowledge_documents(
        thread_id="thread-1",
        knowledge_documents=[
            {
                "kb_id": kb_id,
                "doc_id": doc_id,
                "name": "paper.md",
                "content": content,
                "document_revision": "2026-07-15T00:00:00+00:00",
            }
        ],
        previous_materialized=[
            _managed_record(
                kb_id=kb_id,
                doc_id=doc_id,
                content=content,
                filename=filename,
            )
        ],
    )

    assert result[0]["thread_filename"] == filename
    assert result[0]["synced"] is False
    runtime.upload_bytes.assert_not_awaited()
    runtime.delete_thread_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_materialization_reuploads_same_size_tampered_file():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    content = "trusted"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    service = _service(SimpleNamespace())
    filename = service._build_kb_target_filename(
        kb_id=kb_id,
        doc_id=doc_id,
        name="paper.md",
        content_sha256=digest,
    )
    replacement_filename = filename.replace(".md", "-2.md")
    runtime = SimpleNamespace(
        list_thread_uploads=AsyncMock(
            return_value=[{"filename": filename, "size": len(content)}]
        ),
        get_thread_upload_integrity=AsyncMock(
            return_value={
                "filename": filename,
                "size": len(content),
                "sha256": hashlib.sha256(b"changed").hexdigest(),
            }
        ),
        upload_bytes=AsyncMock(
            return_value={"filename": replacement_filename, "size": len(content)}
        ),
        delete_thread_upload=AsyncMock(return_value={"success": True}),
    )
    service.runtime_service = runtime

    result = await service.sync_knowledge_documents(
        thread_id="thread-1",
        knowledge_documents=[
            {
                "kb_id": kb_id,
                "doc_id": doc_id,
                "name": "paper.md",
                "content": content,
                "document_revision": "2026-07-15T00:00:00+00:00",
            }
        ],
        previous_materialized=[
            _managed_record(
                kb_id=kb_id,
                doc_id=doc_id,
                content=content,
                filename=filename,
            )
        ],
    )

    assert result[0]["thread_filename"] == replacement_filename
    assert result[0]["synced"] is True
    runtime.upload_bytes.assert_awaited_once()
    runtime.delete_thread_upload.assert_awaited_once_with(
        thread_id="thread-1",
        filename=filename,
    )


@pytest.mark.asyncio
async def test_empty_authorized_scope_removes_manifest_managed_files():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    filename = "managed.md"
    runtime = SimpleNamespace(
        list_thread_uploads=AsyncMock(return_value=[{"filename": filename, "size": 1}]),
        upload_bytes=AsyncMock(),
        delete_thread_upload=AsyncMock(return_value={"success": True}),
    )
    service = _service(runtime)

    result = await service.sync_knowledge_documents(
        thread_id="thread-1",
        knowledge_documents=[],
        previous_materialized=[
            _managed_record(
                kb_id=kb_id,
                doc_id=doc_id,
                content="x",
                filename=filename,
            )
        ],
    )

    assert result == []
    runtime.delete_thread_upload.assert_awaited_once_with(
        thread_id="thread-1",
        filename=filename,
    )


@pytest.mark.asyncio
async def test_reconciliation_removes_managed_upload_orphaned_before_manifest_commit():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    service = _service(SimpleNamespace())
    orphaned_filename = service._build_kb_target_filename(
        kb_id=kb_id,
        doc_id=doc_id,
        name="paper.md",
        content_sha256=hashlib.sha256(b"orphaned").hexdigest(),
    )
    runtime = SimpleNamespace(
        list_thread_uploads=AsyncMock(
            return_value=[{"filename": orphaned_filename, "size": 8}]
        ),
        upload_bytes=AsyncMock(),
        delete_thread_upload=AsyncMock(return_value={"success": True}),
    )
    service.runtime_service = runtime

    await service.sync_knowledge_documents(
        thread_id="thread-1",
        knowledge_documents=[],
        previous_materialized=[],
    )

    runtime.delete_thread_upload.assert_awaited_once_with(
        thread_id="thread-1",
        filename=orphaned_filename,
    )


@pytest.mark.asyncio
async def test_batch_storage_failure_aborts_instead_of_shrinking_scope():
    doc_id = str(uuid4())
    document_service = SimpleNamespace(
        get_documents_markdown_batch=AsyncMock(
            return_value={
                "documents": {},
                "document_names": {},
                "document_versions": {},
                "failed": [doc_id],
                "failure_reasons": {doc_id: "storage_error"},
            }
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtime_controller._load_kb_document_batch(
            document_service=document_service,
            kb_id=str(uuid4()),
            doc_ids=[doc_id],
            user_id=str(uuid4()),
        )

    assert exc_info.value.status_code == 503
    assert (
        exc_info.value.detail["error"]["code"]
        == "KNOWLEDGE_MATERIALIZATION_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_batch_rejects_document_outside_requested_scope():
    requested_id = str(uuid4())
    injected_id = str(uuid4())
    document_service = SimpleNamespace(
        get_documents_markdown_batch=AsyncMock(
            return_value={
                "documents": {injected_id: "injected"},
                "document_names": {injected_id: "injected.md"},
                "document_versions": {
                    injected_id: "2026-07-15T00:00:00+00:00"
                },
                "failed": [requested_id],
                "failure_reasons": {requested_id: "not_found"},
            }
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await runtime_controller._load_kb_document_batch(
            document_service=document_service,
            kb_id=str(uuid4()),
            doc_ids=[requested_id],
            user_id=str(uuid4()),
            allow_not_found=True,
        )

    assert exc_info.value.status_code == 503
    assert (
        exc_info.value.detail["error"]["code"]
        == "KNOWLEDGE_MATERIALIZATION_UNAVAILABLE"
    )


@pytest.mark.asyncio
async def test_deferred_cleanup_preserves_previous_files_until_manifest_commit():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    service = _service(SimpleNamespace())
    stale_filename = service._build_kb_target_filename(
        kb_id=kb_id,
        doc_id=doc_id,
        name="old.md",
        content_sha256=hashlib.sha256(b"old").hexdigest(),
    )
    runtime = SimpleNamespace(
        list_thread_uploads=AsyncMock(
            return_value=[{"filename": stale_filename, "size": 3}]
        ),
        upload_bytes=AsyncMock(),
        delete_thread_upload=AsyncMock(),
    )
    service.runtime_service = runtime

    result = await service.sync_knowledge_documents(
        thread_id="thread-1",
        knowledge_documents=[],
        previous_materialized=[
            _managed_record(
                kb_id=kb_id,
                doc_id=doc_id,
                content="old",
                filename=stale_filename,
            )
        ],
        defer_cleanup=True,
    )

    assert result == []
    runtime.delete_thread_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_whole_kb_materialized_documents_are_loaded_before_next_page(
    monkeypatch,
):
    kb_id = str(uuid4())
    user_id = uuid4()
    first_page_ids = [str(uuid4()) for _ in range(100)]
    second_page_id = str(uuid4())
    events: list[tuple[str, int]] = []

    class DocumentService:
        async def list_materialized_document_ids(
            self,
            _kb_id,
            _user_id,
            *,
            page,
            page_size,
        ):
            events.append(("list", page))
            assert 0 < page_size <= 100
            if page == 1:
                return first_page_ids, 101
            assert page == 2
            return [second_page_id], 101

        async def get_documents_markdown_batch(self, doc_ids, _kb_id, _user_id):
            events.append(("batch", len(doc_ids)))
            return {
                "documents": {doc_id: f"content:{doc_id}" for doc_id in doc_ids},
                "document_names": {doc_id: f"{doc_id}.md" for doc_id in doc_ids},
                "document_versions": {
                    doc_id: "2026-07-15T00:00:00+00:00" for doc_id in doc_ids
                },
                "failed": [],
                "failure_reasons": {},
            }

    monkeypatch.setattr(
        runtime_controller,
        "_create_document_service",
        lambda _db: DocumentService(),
    )

    loaded = [
        document
        async for document in runtime_controller._iter_selected_kb_documents(
            session_config={"kbIds": [kb_id], "docIds": []},
            current_user=SimpleNamespace(id=user_id),
            db=object(),
        )
    ]

    assert len(loaded) == 101
    assert events == [
        ("list", 1),
        ("batch", 20),
        ("batch", 20),
        ("batch", 20),
        ("batch", 20),
        ("batch", 20),
        ("list", 2),
        ("batch", 1),
    ]


@pytest.mark.asyncio
async def test_explicit_materialized_documents_are_loaded_in_bounded_batches(
    monkeypatch,
):
    kb_id = str(uuid4())
    user_id = uuid4()
    requested_ids = [str(uuid4()) for _ in range(21)]
    requested_batches: list[list[str]] = []

    async def get_documents_markdown_batch(doc_ids, _kb_id, _user_id):
        requested_batches.append(list(doc_ids))
        return {
            "documents": {doc_id: f"content:{doc_id}" for doc_id in doc_ids},
            "document_names": {doc_id: f"{doc_id}.md" for doc_id in doc_ids},
            "document_versions": {
                doc_id: "2026-07-15T00:00:00+00:00" for doc_id in doc_ids
            },
            "failed": [],
            "failure_reasons": {},
        }

    document_service = SimpleNamespace(
        list_materialized_document_ids=AsyncMock(),
        get_documents_markdown_batch=AsyncMock(
            side_effect=get_documents_markdown_batch
        ),
    )
    monkeypatch.setattr(
        runtime_controller,
        "_create_document_service",
        lambda _db: document_service,
    )

    loaded = [
        document
        async for document in runtime_controller._iter_selected_kb_documents(
            session_config={"kbIds": [kb_id], "docIds": requested_ids},
            current_user=SimpleNamespace(id=user_id),
            db=object(),
        )
    ]

    assert [document["doc_id"] for document in loaded] == requested_ids
    assert requested_batches == [requested_ids[:20], requested_ids[20:]]
    document_service.list_materialized_document_ids.assert_not_awaited()
