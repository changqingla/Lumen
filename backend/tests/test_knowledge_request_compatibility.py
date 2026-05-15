import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.background import BackgroundTasks

from modules.knowledge import controller as knowledge_controller
from schemas.schemas import (
    BatchDocumentMarkdownRequest,
    CompleteDirectUploadRequest,
    InitDirectUploadRequest,
    MoveDocumentRequest,
)


def _user():
    return SimpleNamespace(id=uuid4())


def _assert_invalid_request(exc_info, expected_message: str):
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": expected_message,
        }
    }


@pytest.mark.asyncio
async def test_init_direct_upload_missing_filename_keeps_legacy_400():
    with pytest.raises(HTTPException) as exc_info:
        await knowledge_controller.init_direct_upload(
            kbId=str(uuid4()),
            request=InitDirectUploadRequest(),
            current_user=_user(),
            db=object(),
        )

    _assert_invalid_request(exc_info, "filename is required")


@pytest.mark.asyncio
async def test_complete_direct_upload_missing_doc_id_keeps_legacy_400():
    with pytest.raises(HTTPException) as exc_info:
        await knowledge_controller.complete_direct_upload(
            kbId=str(uuid4()),
            request=CompleteDirectUploadRequest(),
            background_tasks=BackgroundTasks(),
            current_user=_user(),
            db=object(),
        )

    _assert_invalid_request(exc_info, "docId is required")


@pytest.mark.asyncio
async def test_batch_markdown_missing_doc_ids_keeps_legacy_400():
    with pytest.raises(HTTPException) as exc_info:
        await knowledge_controller.get_documents_markdown_batch(
            kbId=str(uuid4()),
            request=BatchDocumentMarkdownRequest(),
            current_user=_user(),
            db=object(),
        )

    _assert_invalid_request(exc_info, "docIds is required")


@pytest.mark.asyncio
async def test_move_document_missing_target_kb_id_keeps_legacy_400():
    with pytest.raises(HTTPException) as exc_info:
        await knowledge_controller.move_document(
            kbId=str(uuid4()),
            docId=str(uuid4()),
            request=MoveDocumentRequest(),
            current_user=_user(),
            db=object(),
        )

    _assert_invalid_request(exc_info, "targetKbId is required")
