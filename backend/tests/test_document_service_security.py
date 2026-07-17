import asyncio
import hashlib
import os
import sys

os.environ["DEBUG"] = "false"

from contextlib import asynccontextmanager
from types import SimpleNamespace
from types import ModuleType
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from modules.knowledge.entities.document import Document
from modules.knowledge.services import kb_service as kb_service_module
from modules.knowledge.services import document_service as document_service_module
from modules.knowledge.services.document_service import DocumentService
from utils.es_utils import get_user_es_index
from utils.minio_client import ObjectMetadata


def test_normalize_filename_requires_explicit_filename():
    service = DocumentService(db=object())

    with pytest.raises(HTTPException) as exc_info:
        service._normalize_filename("")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "INVALID_REQUEST"


def test_normalize_filename_keeps_basename_without_generating_name():
    service = DocumentService(db=object())

    assert service._normalize_filename("../report.pdf") == "report.pdf"


def test_extract_docx_content_from_path_raises_for_empty_docx(monkeypatch, tmp_path):
    source_path = tmp_path / "empty.docx"
    source_path.write_bytes(b"not-loaded-by-the-service")

    class _EmptyDocx:
        paragraphs = []
        tables = []

    fake_docx = ModuleType("docx")
    fake_docx.Document = lambda _path: _EmptyDocx()
    monkeypatch.setitem(sys.modules, "docx", fake_docx)

    service = DocumentService(db=object())

    with pytest.raises(ValueError, match="no extractable text"):
        service._extract_docx_content_from_path(source_path)


def test_extract_docx_content_from_path_uses_path_api(monkeypatch, tmp_path):
    source_path = tmp_path / "report.docx"
    source_path.write_bytes(b"not-loaded-by-the-service")
    received = []

    class _Docx:
        paragraphs = [SimpleNamespace(text="Paragraph")]
        tables = []

    fake_docx = ModuleType("docx")
    fake_docx.Document = lambda source: received.append(source) or _Docx()
    monkeypatch.setitem(sys.modules, "docx", fake_docx)

    service = DocumentService(db=object())

    assert service._extract_docx_content_from_path(source_path) == "Paragraph"
    assert received == [str(source_path)]


def test_extract_doc_content_from_path_uses_tika_file_api(monkeypatch, tmp_path):
    source_path = tmp_path / "legacy.doc"
    source_path.write_bytes(b"not-loaded-by-the-service")

    def from_file(source):
        return {"content": f" First \n\n Second from {source} "}

    fake_tika = ModuleType("tika")
    fake_tika.parser = SimpleNamespace(from_file=from_file)
    monkeypatch.setitem(sys.modules, "tika", fake_tika)

    service = DocumentService(db=object())

    assert service._extract_doc_content_from_path(source_path) == (
        f"First\n\nSecond from {source_path}"
    )


def test_read_text_file_decodes_incrementally_with_fallback(tmp_path):
    source_path = tmp_path / "notes.txt"
    expected = "Lumen 中文内容"
    source_path.write_bytes(expected.encode("gbk"))

    assert DocumentService._read_text_file(source_path) == expected


def test_temporary_markdown_file_cleans_up_after_error():
    service = DocumentService(db=object())

    with pytest.raises(RuntimeError, match="processing failed"):
        with service._temporary_markdown_file("content", "doc-1") as temp_path:
            assert temp_path.read_text(encoding="utf-8") == "content"
            raise RuntimeError("processing failed")

    assert not temp_path.exists()


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


@pytest.mark.asyncio
async def test_batch_markdown_uses_one_strict_query_and_reports_every_failure(
    monkeypatch,
    tmp_path,
):
    kb_id = str(uuid4())
    ready_id = str(uuid4())
    failed_status_id = str(uuid4())
    processing_status_id = str(uuid4())
    embedding_status_id = str(uuid4())
    download_failure_id = str(uuid4())
    decode_failure_id = str(uuid4())
    empty_markdown_id = str(uuid4())
    missing_or_ineligible_id = str(uuid4())
    missing_markdown_id = str(uuid4())
    missing_version_id = str(uuid4())
    invalid_hash_id = str(uuid4())
    invalid_id = "not-a-document-uuid"
    rows = [
        SimpleNamespace(
            id=ready_id,
            name="ready.md",
            status=Document.STATUS_READY,
            markdown_path=f"reader-uploads/markdown/{ready_id}.md",
            materialization_revision=1,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=failed_status_id,
            name="failed-but-materialized.md",
            status=Document.STATUS_FAILED,
            markdown_path=f"reader-uploads/markdown/{failed_status_id}.md",
            materialization_revision=2,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=processing_status_id,
            name="processing-but-materialized.md",
            status=Document.STATUS_PROCESSING,
            markdown_path=f"reader-uploads/markdown/{processing_status_id}.md",
            materialization_revision=3,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=embedding_status_id,
            name="embedding-but-materialized.md",
            status=Document.STATUS_EMBEDDING,
            markdown_path=f"reader-uploads/markdown/{embedding_status_id}.md",
            materialization_revision=4,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=download_failure_id,
            name="broken.md",
            status=Document.STATUS_READY,
            markdown_path=f"reader-uploads/markdown/{download_failure_id}.md",
            materialization_revision=1,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=decode_failure_id,
            name="undecodable.md",
            status=Document.STATUS_READY,
            markdown_path=f"reader-uploads/markdown/{decode_failure_id}.md",
            materialization_revision=1,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=empty_markdown_id,
            name="empty.md",
            status=Document.STATUS_FAILED,
            markdown_path=f"reader-uploads/markdown/{empty_markdown_id}.md",
            materialization_revision=1,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=missing_markdown_id,
            name="missing.md",
            status=Document.STATUS_READY,
            markdown_path=None,
            materialization_revision=1,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=missing_version_id,
            name="missing-version.md",
            status=Document.STATUS_PROCESSING,
            markdown_path=f"reader-uploads/markdown/{missing_version_id}.md",
            materialization_revision=0,
            markdown_sha256=None,
        ),
        SimpleNamespace(
            id=invalid_hash_id,
            name="invalid-hash.md",
            status=Document.STATUS_EMBEDDING,
            markdown_path=f"reader-uploads/markdown/{invalid_hash_id}.md",
            materialization_revision=1,
            markdown_sha256="not-a-sha256-digest",
        ),
    ]
    db = SimpleNamespace(execute=AsyncMock(return_value=_RowsResult(rows)))
    service = DocumentService(db=db)
    service._verify_kb_access = AsyncMock(return_value=SimpleNamespace(id=kb_id))
    service.doc_repo.get_by_id = AsyncMock()
    downloaded = []

    @asynccontextmanager
    async def fake_temporary_download(object_name, **kwargs):
        downloaded.append((object_name, kwargs))
        if download_failure_id in object_name:
            raise RuntimeError("MinIO unavailable")
        path_doc_id = object_name.rsplit("/", 1)[-1].removesuffix(".md")
        temp_path = tmp_path / f"{path_doc_id}.md"
        content = "   \n" if empty_markdown_id in object_name else "ready content"
        temp_path.write_text(content, encoding="utf-8")
        try:
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)

    monkeypatch.setattr(
        document_service_module,
        "temporary_download",
        fake_temporary_download,
    )
    real_read_text_file = service._read_text_file

    def read_text_file(temp_path):
        if decode_failure_id in str(temp_path):
            raise UnicodeError("decode failed")
        return real_read_text_file(temp_path)

    monkeypatch.setattr(service, "_read_text_file", read_text_file)

    result = await service.get_documents_markdown_batch(
        [
            ready_id,
            failed_status_id,
            processing_status_id,
            embedding_status_id,
            download_failure_id,
            decode_failure_id,
            empty_markdown_id,
            missing_or_ineligible_id,
            missing_markdown_id,
            missing_version_id,
            invalid_hash_id,
            invalid_id,
        ],
        kb_id,
        str(uuid4()),
    )

    assert result == {
        "documents": {
            ready_id: "ready content",
            failed_status_id: "ready content",
            processing_status_id: "ready content",
            embedding_status_id: "ready content",
        },
        "document_names": {
            ready_id: "ready.md",
            failed_status_id: "failed-but-materialized.md",
            processing_status_id: "processing-but-materialized.md",
            embedding_status_id: "embedding-but-materialized.md",
        },
        "document_versions": {
            ready_id: "1",
            failed_status_id: "2",
            processing_status_id: "3",
            embedding_status_id: "4",
        },
        "failed": [
            download_failure_id,
            decode_failure_id,
            empty_markdown_id,
            missing_or_ineligible_id,
            missing_markdown_id,
            missing_version_id,
            invalid_hash_id,
            invalid_id,
        ],
        "failure_reasons": {
            download_failure_id: "storage_error",
            decode_failure_id: "decode_error",
            empty_markdown_id: "empty_markdown",
            missing_or_ineligible_id: "not_found",
            missing_markdown_id: "missing_markdown",
            missing_version_id: "missing_version",
            invalid_hash_id: "invalid_hash",
            invalid_id: "invalid_id",
        },
    }
    db.execute.assert_awaited_once()
    statement = str(db.execute.await_args.args[0])
    assert "kb_documents.status" not in statement
    assert "kb_documents.markdown_path" in statement
    service.doc_repo.get_by_id.assert_not_awaited()
    assert len(downloaded) == 7
    assert all(
        kwargs["max_bytes"] == document_service_module.settings.MAX_UPLOAD_SIZE
        for _, kwargs in downloaded
    )


@pytest.mark.asyncio
async def test_batch_markdown_empty_request_does_not_query_documents(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock())
    service = DocumentService(db=db)
    service._verify_kb_access = AsyncMock(return_value=SimpleNamespace())

    result = await service.get_documents_markdown_batch([], str(uuid4()), str(uuid4()))

    assert result == {
        "documents": {},
        "document_names": {},
        "document_versions": {},
        "failed": [],
        "failure_reasons": {},
    }
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_markdown_limits_concurrent_object_downloads(monkeypatch, tmp_path):
    kb_id = str(uuid4())
    doc_ids = [str(uuid4()) for _ in range(9)]
    rows = [
        SimpleNamespace(
            id=doc_id,
            name=f"{doc_id}.md",
            status=Document.STATUS_READY,
            markdown_path=f"reader-uploads/markdown/{doc_id}.md",
            materialization_revision=1,
            markdown_sha256=None,
        )
        for doc_id in doc_ids
    ]
    db = SimpleNamespace(execute=AsyncMock(return_value=_RowsResult(rows)))
    service = DocumentService(db=db)
    service._verify_kb_access = AsyncMock(return_value=SimpleNamespace())
    active_downloads = 0
    max_active_downloads = 0

    @asynccontextmanager
    async def fake_temporary_download(object_name, **kwargs):
        nonlocal active_downloads, max_active_downloads
        active_downloads += 1
        max_active_downloads = max(max_active_downloads, active_downloads)
        temp_path = tmp_path / f"{uuid4()}.md"
        try:
            await asyncio.sleep(0.01)
            temp_path.write_text(object_name, encoding="utf-8")
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)
            active_downloads -= 1

    monkeypatch.setattr(
        document_service_module,
        "temporary_download",
        fake_temporary_download,
    )

    result = await service.get_documents_markdown_batch(
        doc_ids,
        kb_id,
        str(uuid4()),
    )

    assert set(result["documents"]) == set(doc_ids)
    assert set(result["document_versions"]) == set(doc_ids)
    assert result["failed"] == []
    assert 1 < max_active_downloads <= service.MARKDOWN_DOWNLOAD_CONCURRENCY


@pytest.mark.asyncio
async def test_batch_markdown_rejects_object_content_that_differs_from_database_hash(
    monkeypatch,
    tmp_path,
):
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    row = SimpleNamespace(
        id=doc_id,
        name="document.md",
        status=Document.STATUS_READY,
        markdown_path=f"reader-uploads/markdown/{doc_id}.md",
        materialization_revision=4,
        markdown_sha256=hashlib.sha256(b"expected").hexdigest(),
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_RowsResult([row])))
    service = DocumentService(db=db)
    service._verify_kb_access = AsyncMock(return_value=SimpleNamespace())

    @asynccontextmanager
    async def fake_temporary_download(_object_name, **_kwargs):
        temp_path = tmp_path / "tampered.md"
        temp_path.write_text("tampered", encoding="utf-8")
        yield temp_path

    monkeypatch.setattr(
        document_service_module,
        "temporary_download",
        fake_temporary_download,
    )

    result = await service.get_documents_markdown_batch(
        [doc_id],
        kb_id,
        str(uuid4()),
    )

    assert result["documents"] == {}
    assert result["failed"] == [doc_id]
    assert result["failure_reasons"] == {doc_id: "integrity_mismatch"}


@pytest.mark.asyncio
async def test_complete_direct_upload_rejects_actual_size_mismatch(monkeypatch):
    user_id = str(uuid4())
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    doc = SimpleNamespace(
        id=doc_id,
        name="report.pdf",
        size=10,
        status=Document.STATUS_UPLOADING,
        file_path="reader-uploads/kb/owner/report.pdf",
    )
    service = DocumentService(db=object())
    monkeypatch.setattr(
        service,
        "_verify_kb_write_access",
        AsyncMock(return_value=SimpleNamespace(owner_id=uuid4())),
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=doc)
    service.doc_repo.update_status = AsyncMock()
    monkeypatch.setattr(
        document_service_module,
        "get_object_metadata",
        AsyncMock(
            return_value=ObjectMetadata(
                size=11, content_type="application/pdf", etag="etag"
            )
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        await service.complete_direct_upload(kb_id, user_id, doc_id)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "UPLOAD_SIZE_MISMATCH"
    service.doc_repo.update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_direct_upload_persists_queue_state_before_enqueue(monkeypatch):
    user_id = str(uuid4())
    owner_id = uuid4()
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    doc = SimpleNamespace(
        id=doc_id,
        name="report.pdf",
        size=10,
        status=Document.STATUS_UPLOADING,
        file_path="reader-uploads/kb/owner/report.pdf",
    )
    service = DocumentService(db=object())
    monkeypatch.setattr(
        service,
        "_verify_kb_write_access",
        AsyncMock(return_value=SimpleNamespace(owner_id=owner_id)),
    )
    service.doc_repo.get_by_id = AsyncMock(return_value=doc)
    service.doc_repo.update_status = AsyncMock()
    monkeypatch.setattr(
        document_service_module,
        "get_object_metadata",
        AsyncMock(
            return_value=ObjectMetadata(
                size=10, content_type="application/pdf", etag="etag"
            )
        ),
    )
    enqueue = AsyncMock(return_value=1)
    monkeypatch.setattr(document_service_module, "enqueue_document_task", enqueue)

    result = await service.complete_direct_upload(kb_id, user_id, doc_id)

    assert result["id"] == doc_id
    service.doc_repo.update_status.assert_awaited_once_with(
        doc,
        Document.STATUS_QUEUED,
        error_message=None,
    )
    enqueue.assert_awaited_once_with(doc_id)
    assert result["status"] == Document.STATUS_PROCESSING


@pytest.mark.asyncio
async def test_retry_document_persists_queue_state_before_enqueue(monkeypatch):
    acting_user_id = str(uuid4())
    owner_id = uuid4()
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    doc = SimpleNamespace(
        id=doc_id,
        kb_id=kb_id,
        name="report.pdf",
        status=Document.STATUS_FAILED,
        file_path="reader-uploads/kb/owner/report.pdf",
        markdown_path=None,
    )
    kb = SimpleNamespace(id=kb_id, owner_id=owner_id)
    service = DocumentService(db=object())
    monkeypatch.setattr(service, "_verify_kb_write_access", AsyncMock(return_value=kb))
    service.doc_repo.get_by_id = AsyncMock(return_value=doc)
    service.doc_repo.update_status = AsyncMock()
    enqueue = AsyncMock(return_value=1)
    monkeypatch.setattr(document_service_module, "enqueue_document_task", enqueue)

    response = await service.retry_document(doc_id, kb_id, acting_user_id)

    assert response["status"] == Document.STATUS_PROCESSING
    service.doc_repo.update_status.assert_awaited_once_with(
        doc,
        Document.STATUS_QUEUED,
        error_message=None,
    )
    enqueue.assert_awaited_once_with(doc_id)


@pytest.mark.asyncio
async def test_delete_document_uses_kb_owner_index(monkeypatch):
    acting_user_id = str(uuid4())
    owner_id = uuid4()
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    kb = SimpleNamespace(id=kb_id, owner_id=owner_id)
    doc = SimpleNamespace(
        id=doc_id,
        kb_id=kb_id,
        name="report.pdf",
        status=Document.STATUS_READY,
        file_path=None,
        markdown_path=None,
    )

    service = DocumentService(db=object())
    monkeypatch.setattr(service, "_verify_kb_write_access", AsyncMock(return_value=kb))
    service.doc_repo.get_by_id = AsyncMock(return_value=doc)
    service.doc_repo.delete = AsyncMock()
    service.kb_repo.sync_contents_count = AsyncMock()
    delete_from_es = AsyncMock()
    monkeypatch.setattr(
        document_service_module.DocumentProcessService,
        "delete_document_from_es",
        delete_from_es,
    )

    await service.delete_document(doc_id, kb_id, acting_user_id)

    delete_from_es.assert_awaited_once_with(doc_id, get_user_es_index(str(owner_id)))
    service.kb_repo.sync_contents_count.assert_awaited_once_with(kb_id)


@pytest.mark.asyncio
async def test_delete_kb_uses_document_protocol_for_every_document(monkeypatch):
    acting_user_id = str(uuid4())
    kb_id = str(uuid4())
    kb = SimpleNamespace(id=kb_id, owner_id=uuid4())
    document_ids = [str(uuid4()) for _ in range(25)]
    service = kb_service_module.KnowledgeBaseService(db=object())
    monkeypatch.setattr(
        service,
        "_verify_kb_write_access",
        AsyncMock(return_value=kb),
    )
    service.doc_repo.get_all_doc_ids = AsyncMock(return_value=document_ids)
    service.kb_repo.delete = AsyncMock()
    delete_document = AsyncMock()
    monkeypatch.setattr(
        document_service_module,
        "DocumentService",
        lambda _db: SimpleNamespace(delete_document=delete_document),
    )

    await service.delete_kb(kb_id, acting_user_id)

    assert delete_document.await_count == 25
    assert [call.args[0] for call in delete_document.await_args_list] == document_ids
    assert all(
        call.args[1:] == (kb_id, acting_user_id)
        for call in delete_document.await_args_list
    )
    service.kb_repo.delete.assert_awaited_once_with(kb)


@pytest.mark.asyncio
async def test_delete_kb_keeps_parent_when_document_cancellation_is_pending(
    monkeypatch,
):
    acting_user_id = str(uuid4())
    kb_id = str(uuid4())
    kb = SimpleNamespace(id=kb_id, owner_id=uuid4())
    service = kb_service_module.KnowledgeBaseService(db=object())
    monkeypatch.setattr(
        service,
        "_verify_kb_write_access",
        AsyncMock(return_value=kb),
    )
    service.doc_repo.get_all_doc_ids = AsyncMock(return_value=[str(uuid4())])
    service.kb_repo.delete = AsyncMock()
    cancellation_pending = HTTPException(
        status_code=409,
        detail={"error": {"code": "PROCESSING_CANCELLATION_PENDING"}},
    )
    monkeypatch.setattr(
        document_service_module,
        "DocumentService",
        lambda _db: SimpleNamespace(
            delete_document=AsyncMock(side_effect=cancellation_pending)
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.delete_kb(kb_id, acting_user_id)

    assert exc_info.value.status_code == 409
    service.kb_repo.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_document_requires_owner_of_both_kbs(monkeypatch):
    user_id = str(uuid4())
    source_owner_id = uuid4()
    target_owner_id = uuid4()
    source_kb_id = str(uuid4())
    target_kb_id = str(uuid4())

    service = DocumentService(db=object())
    monkeypatch.setattr(
        service,
        "_verify_kb_write_access",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id=source_kb_id, owner_id=source_owner_id),
                SimpleNamespace(id=target_kb_id, owner_id=target_owner_id),
            ]
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.move_document("doc-1", source_kb_id, target_kb_id, user_id)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_kb_avatar_rejects_svg_upload(monkeypatch):
    user_id = str(uuid4())
    kb_id = str(uuid4())
    service = kb_service_module.KnowledgeBaseService(db=object())
    service.kb_repo.get_by_id = AsyncMock(
        return_value=SimpleNamespace(id=kb_id, owner_id=user_id)
    )
    service.kb_repo.update = AsyncMock()
    upload_file = AsyncMock()
    monkeypatch.setattr(kb_service_module, "upload_file", upload_file, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await service.upload_avatar(
            kb_id=kb_id,
            user_id=user_id,
            file_data=b"<svg></svg>",
            filename="avatar.svg",
            content_type="image/svg+xml",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "VALIDATION_ERROR"
    upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_kb_avatar_rejects_oversized_upload(monkeypatch):
    user_id = str(uuid4())
    kb_id = str(uuid4())
    service = kb_service_module.KnowledgeBaseService(db=object())
    service.kb_repo.get_by_id = AsyncMock(
        return_value=SimpleNamespace(id=kb_id, owner_id=user_id)
    )
    service.kb_repo.update = AsyncMock()
    upload_file = AsyncMock()
    monkeypatch.setattr(kb_service_module, "upload_file", upload_file, raising=False)
    monkeypatch.setattr(
        kb_service_module,
        "settings",
        SimpleNamespace(MAX_AVATAR_SIZE=3),
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.upload_avatar(
            kb_id=kb_id,
            user_id=user_id,
            file_data=b"\x89PNG\r\n\x1a\nextra",
            filename="avatar.png",
            content_type="image/png",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "VALIDATION_ERROR"
    upload_file.assert_not_awaited()
