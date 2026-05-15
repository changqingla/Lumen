import os
import sys

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from types import ModuleType
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from modules.knowledge.entities.document import Document
from modules.knowledge.services import kb_service as kb_service_module
from modules.knowledge.services import document_service as document_service_module
from modules.knowledge.services.document_service import DocumentService
from utils.es_utils import get_user_es_index


class _BackgroundTasks:
    def __init__(self):
        self.add_task = MagicMock()


def test_normalize_filename_requires_explicit_filename():
    service = DocumentService(db=object())

    with pytest.raises(HTTPException) as exc_info:
        service._normalize_filename("")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "INVALID_REQUEST"


def test_normalize_filename_keeps_basename_without_generating_name():
    service = DocumentService(db=object())

    assert service._normalize_filename("../report.pdf") == "report.pdf"


def test_extract_docx_content_raises_for_empty_docx(monkeypatch):
    class _EmptyDocx:
        paragraphs = []
        tables = []

    fake_docx = ModuleType("docx")
    fake_docx.Document = lambda _stream: _EmptyDocx()
    monkeypatch.setitem(sys.modules, "docx", fake_docx)

    service = DocumentService(db=object())

    with pytest.raises(ValueError, match="no extractable text"):
        service._extract_docx_content(b"empty-docx")


@pytest.mark.asyncio
async def test_retry_document_uses_kb_owner_index(monkeypatch):
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
    background_tasks = _BackgroundTasks()

    service = DocumentService(db=object())
    monkeypatch.setattr(service, "_verify_kb_write_access", AsyncMock(return_value=kb))
    service.doc_repo.get_by_id = AsyncMock(return_value=doc)
    service.doc_repo.update_status = AsyncMock()

    response = await service.retry_document(doc_id, kb_id, acting_user_id, background_tasks)

    assert response["status"] == Document.STATUS_PROCESSING
    scheduled_args = background_tasks.add_task.call_args.args
    assert scheduled_args[3] == get_user_es_index(str(owner_id))


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
    service.kb_repo.increment_contents_count = AsyncMock()
    delete_from_es = AsyncMock()
    monkeypatch.setattr(document_service_module.DocumentProcessService, "delete_document_from_es", delete_from_es)

    await service.delete_document(doc_id, kb_id, acting_user_id)

    delete_from_es.assert_awaited_once_with(doc_id, get_user_es_index(str(owner_id)))


@pytest.mark.asyncio
async def test_delete_kb_uses_owner_index_for_cleanup(monkeypatch):
    acting_user_id = str(uuid4())
    owner_id = uuid4()
    kb_id = str(uuid4())
    kb = SimpleNamespace(id=kb_id, owner_id=owner_id)
    docs = [SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())]

    service = kb_service_module.KnowledgeBaseService(db=object())
    monkeypatch.setattr(service, "_verify_kb_write_access", AsyncMock(return_value=kb))
    service.doc_repo.list_documents = AsyncMock(return_value=(docs, len(docs)))
    service.kb_repo.delete = AsyncMock()
    delete_from_es = AsyncMock()
    monkeypatch.setattr(kb_service_module.DocumentProcessService, "delete_document_from_es", delete_from_es)

    await service.delete_kb(kb_id, acting_user_id)

    expected_index = get_user_es_index(str(owner_id))
    delete_from_es.assert_any_await(str(docs[0].id), expected_index)
    delete_from_es.assert_any_await(str(docs[1].id), expected_index)


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
    service.kb_repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=kb_id, owner_id=user_id))
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
    service.kb_repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=kb_id, owner_id=user_id))
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
