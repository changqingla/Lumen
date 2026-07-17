from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.chat.services.runtime_knowledge_scope_service import (
    KnowledgeDocumentRevision,
    KnowledgeScopeSnapshot,
    RuntimeKnowledgeScopeService,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


@pytest.mark.asyncio
async def test_resolve_current_scope_uses_accessible_materialized_document_revision():
    user_id = uuid4()
    kb_id = uuid4()
    doc_id = uuid4()
    revision = 7
    digest = "a" * 64
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result([]),
                _Result([(kb_id,)]),
                _Result(
                    [
                        SimpleNamespace(
                            id=doc_id,
                            kb_id=kb_id,
                            status="ready",
                            markdown_path="markdown/paper.md",
                            materialization_revision=revision,
                            markdown_sha256=digest,
                        )
                    ]
                ),
            ]
        )
    )
    service = RuntimeKnowledgeScopeService(db)

    scope = await service.resolve_current_scope(
        session_config={"kbIds": [str(kb_id)], "docIds": [str(doc_id)]},
        current_user=SimpleNamespace(id=user_id, is_admin=False),
    )

    assert scope == KnowledgeScopeSnapshot(
        kb_ids=(str(kb_id),),
        requested_doc_ids=(str(doc_id),),
        documents=(
            KnowledgeDocumentRevision(
                kb_id=str(kb_id),
                doc_id=str(doc_id),
                document_revision=str(revision),
                content_sha256=digest,
            ),
        ),
    )
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_resolve_current_scope_rejects_revoked_kb_access():
    user_id = uuid4()
    kb_id = uuid4()
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Result([]), _Result([])]))
    service = RuntimeKnowledgeScopeService(db)

    with pytest.raises(HTTPException) as exc_info:
        await service.resolve_current_scope(
            session_config={"kbIds": [str(kb_id)], "docIds": []},
            current_user=SimpleNamespace(id=user_id, is_admin=False),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "KNOWLEDGE_ACCESS_REVOKED"


@pytest.mark.parametrize("document_status", ["failed", "processing"])
@pytest.mark.asyncio
async def test_resolve_current_scope_allows_explicit_materialized_document_regardless_of_status(
    document_status,
):
    user_id = uuid4()
    kb_id = uuid4()
    doc_id = uuid4()
    revision = 3
    digest = "b" * 64
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result([]),
                _Result([(kb_id,)]),
                _Result(
                    [
                        SimpleNamespace(
                            id=doc_id,
                            kb_id=kb_id,
                            status=document_status,
                            markdown_path="markdown/old.md",
                            materialization_revision=revision,
                            markdown_sha256=digest,
                        )
                    ]
                ),
            ]
        )
    )
    service = RuntimeKnowledgeScopeService(db)

    scope = await service.resolve_current_scope(
        session_config={"kbIds": [str(kb_id)], "docIds": [str(doc_id)]},
        current_user=SimpleNamespace(id=user_id, is_admin=False),
    )

    assert scope == KnowledgeScopeSnapshot(
        kb_ids=(str(kb_id),),
        requested_doc_ids=(str(doc_id),),
        documents=(
            KnowledgeDocumentRevision(
                kb_id=str(kb_id),
                doc_id=str(doc_id),
                document_revision=str(revision),
                content_sha256=digest,
            ),
        ),
    )


@pytest.mark.parametrize("markdown_path", [None, ""])
@pytest.mark.asyncio
async def test_resolve_current_scope_rejects_explicit_document_without_markdown_path(
    markdown_path,
):
    user_id = uuid4()
    kb_id = uuid4()
    doc_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result([]),
                _Result([(kb_id,)]),
                _Result(
                    [
                        SimpleNamespace(
                            id=doc_id,
                            kb_id=kb_id,
                            status="failed",
                            markdown_path=markdown_path,
                            materialization_revision=1,
                            markdown_sha256="c" * 64,
                        )
                    ]
                ),
            ]
        )
    )
    service = RuntimeKnowledgeScopeService(db)

    with pytest.raises(HTTPException) as exc_info:
        await service.resolve_current_scope(
            session_config={"kbIds": [str(kb_id)], "docIds": [str(doc_id)]},
            current_user=SimpleNamespace(id=user_id, is_admin=False),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_KNOWLEDGE_STALE"


@pytest.mark.parametrize("revision", [-1, 0])
@pytest.mark.asyncio
async def test_resolve_current_scope_rejects_explicit_document_without_stable_revision(
    revision,
):
    user_id = uuid4()
    kb_id = uuid4()
    doc_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result([]),
                _Result([(kb_id,)]),
                _Result(
                    [
                        SimpleNamespace(
                            id=doc_id,
                            kb_id=kb_id,
                            status="processing",
                            markdown_path="markdown/stable.md",
                            materialization_revision=revision,
                            markdown_sha256="d" * 64,
                        )
                    ]
                ),
            ]
        )
    )
    service = RuntimeKnowledgeScopeService(db)

    with pytest.raises(HTTPException) as exc_info:
        await service.resolve_current_scope(
            session_config={"kbIds": [str(kb_id)], "docIds": [str(doc_id)]},
            current_user=SimpleNamespace(id=user_id, is_admin=False),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_KNOWLEDGE_STALE"


@pytest.mark.asyncio
async def test_resolve_current_scope_rejects_invalid_materialized_digest():
    user_id = uuid4()
    kb_id = uuid4()
    doc_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result([]),
                _Result([(kb_id,)]),
                _Result(
                    [
                        SimpleNamespace(
                            id=doc_id,
                            kb_id=kb_id,
                            status="failed",
                            markdown_path="markdown/paper.md",
                            materialization_revision=1,
                            markdown_sha256="invalid",
                        )
                    ]
                ),
            ]
        )
    )
    service = RuntimeKnowledgeScopeService(db)

    with pytest.raises(HTTPException) as exc_info:
        await service.resolve_current_scope(
            session_config={"kbIds": [str(kb_id)], "docIds": [str(doc_id)]},
            current_user=SimpleNamespace(id=user_id, is_admin=False),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_KNOWLEDGE_STALE"


@pytest.mark.asyncio
async def test_resolve_current_scope_whole_kb_queries_stable_markdown_without_status_filter():
    user_id = uuid4()
    kb_id = uuid4()
    failed_doc_id = uuid4()
    processing_doc_id = uuid4()
    rows = [
        SimpleNamespace(
            id=failed_doc_id,
            kb_id=kb_id,
            status="failed",
            markdown_path="markdown/failed.md",
            materialization_revision=2,
            markdown_sha256="e" * 64,
        ),
        SimpleNamespace(
            id=processing_doc_id,
            kb_id=kb_id,
            status="processing",
            markdown_path="markdown/processing.md",
            materialization_revision=4,
            markdown_sha256="f" * 64,
        ),
    ]
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result([]),
                _Result([(kb_id,)]),
                _Result(rows),
            ]
        )
    )
    service = RuntimeKnowledgeScopeService(db)

    scope = await service.resolve_current_scope(
        session_config={"kbIds": [str(kb_id)], "docIds": []},
        current_user=SimpleNamespace(id=user_id, is_admin=False),
    )

    expected_documents = tuple(
        sorted(
            (
                KnowledgeDocumentRevision(
                    str(kb_id),
                    str(failed_doc_id),
                    "2",
                    "e" * 64,
                ),
                KnowledgeDocumentRevision(
                    str(kb_id),
                    str(processing_doc_id),
                    "4",
                    "f" * 64,
                ),
            ),
            key=lambda item: (item.kb_id, item.doc_id),
        )
    )
    assert scope == KnowledgeScopeSnapshot(
        kb_ids=(str(kb_id),),
        requested_doc_ids=(),
        documents=expected_documents,
    )
    assert scope.scope_mode == "all_materialized"

    document_statement = str(db.execute.await_args_list[2].args[0])
    assert "kb_documents.markdown_path IS NOT NULL" in document_statement
    assert "btrim(kb_documents.markdown_path) !=" in document_statement
    assert "kb_documents.materialization_revision >=" in document_statement
    assert "kb_documents.status" not in document_statement


def test_validate_manifest_requires_exact_revision_hash_size_and_filename():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    revision = "2026-07-15T00:00:00+00:00"
    digest = "a" * 64
    filename = f"kb__{kb_id}__{doc_id}__{digest[:16]}__paper.md"
    scope = KnowledgeScopeSnapshot(
        kb_ids=(kb_id,),
        requested_doc_ids=(doc_id,),
        documents=(
            KnowledgeDocumentRevision(
                kb_id=kb_id,
                doc_id=doc_id,
                document_revision=revision,
                content_sha256=digest,
            ),
        ),
    )

    manifest = RuntimeKnowledgeScopeService.validate_manifest(
        scope=scope,
        raw_manifest=[
            {
                "kb_id": kb_id,
                "doc_id": doc_id,
                "document_revision": revision,
                "content_sha256": digest,
                "thread_filename": filename,
                "size_bytes": 12,
            }
        ],
    )

    assert manifest[0].thread_filename == filename
    assert manifest[0].size_bytes == 12


def test_validate_manifest_rejects_digest_that_differs_from_database_identity():
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    expected_digest = "a" * 64
    prepared_digest = "b" * 64
    scope = KnowledgeScopeSnapshot(
        kb_ids=(kb_id,),
        requested_doc_ids=(doc_id,),
        documents=(
            KnowledgeDocumentRevision(
                kb_id=kb_id,
                doc_id=doc_id,
                document_revision="3",
                content_sha256=expected_digest,
            ),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        RuntimeKnowledgeScopeService.validate_manifest(
            scope=scope,
            raw_manifest=[
                {
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "document_revision": "3",
                    "content_sha256": prepared_digest,
                    "thread_filename": (
                        f"kb__{kb_id}__{doc_id}__{prepared_digest[:16]}__paper.md"
                    ),
                    "size_bytes": 12,
                }
            ],
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_KNOWLEDGE_STALE"


@pytest.mark.parametrize("field", ["document_revision", "size_bytes", "content_sha256"])
def test_validate_manifest_rejects_missing_integrity_field(field):
    kb_id = str(uuid4())
    doc_id = str(uuid4())
    digest = "a" * 64
    record = {
        "kb_id": kb_id,
        "doc_id": doc_id,
        "document_revision": "2026-07-15T00:00:00+00:00",
        "content_sha256": digest,
        "thread_filename": f"kb__{kb_id}__{doc_id}__{digest[:16]}__paper.md",
        "size_bytes": 12,
    }
    record.pop(field)
    scope = KnowledgeScopeSnapshot(
        kb_ids=(kb_id,),
        requested_doc_ids=(doc_id,),
        documents=(
            KnowledgeDocumentRevision(
                kb_id=kb_id,
                doc_id=doc_id,
                document_revision="2026-07-15T00:00:00+00:00",
            ),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        RuntimeKnowledgeScopeService.validate_manifest(
            scope=scope,
            raw_manifest=[record],
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_PREPARATION_REQUIRED"


def test_validate_manifest_rejects_new_materialized_document_outside_old_snapshot():
    kb_id = str(uuid4())
    old_doc_id = str(uuid4())
    new_doc_id = str(uuid4())
    revision = "2026-07-15T00:00:00+00:00"
    digest = "a" * 64
    scope = KnowledgeScopeSnapshot(
        kb_ids=(kb_id,),
        requested_doc_ids=(),
        documents=(
            KnowledgeDocumentRevision(kb_id, old_doc_id, revision),
            KnowledgeDocumentRevision(kb_id, new_doc_id, revision),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        RuntimeKnowledgeScopeService.validate_manifest(
            scope=scope,
            raw_manifest=[
                {
                    "kb_id": kb_id,
                    "doc_id": old_doc_id,
                    "document_revision": revision,
                    "content_sha256": digest,
                    "thread_filename": (
                        f"kb__{kb_id}__{old_doc_id}__{digest[:16]}__old.md"
                    ),
                    "size_bytes": 12,
                }
            ],
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "RUNTIME_KNOWLEDGE_STALE"
