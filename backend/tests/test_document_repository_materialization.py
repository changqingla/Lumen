from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from modules.knowledge.repositories.document_repository import DocumentRepository


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _CountResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _ScalarsResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.mark.asyncio
async def test_markdown_identity_advances_only_when_content_changes():
    document = SimpleNamespace(
        id=uuid4(),
        markdown_path=None,
        markdown_sha256=None,
        materialization_revision=0,
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarResult(document)),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    repository = DocumentRepository(db)
    first_digest = "a" * 64

    await repository.update_markdown_path(
        document,
        "bucket/markdown/document.md",
        first_digest,
    )
    assert document.materialization_revision == 1
    assert document.markdown_sha256 == first_digest

    await repository.update_markdown_path(
        document,
        "bucket/markdown/moved-document.md",
        first_digest,
    )
    assert document.materialization_revision == 1

    await repository.update_markdown_path(
        document,
        "bucket/markdown/moved-document.md",
        "b" * 64,
    )
    assert document.materialization_revision == 2
    assert db.commit.await_count == 3
    assert db.refresh.await_count == 3


@pytest.mark.asyncio
async def test_markdown_identity_rejects_invalid_digest_before_database_access():
    db = SimpleNamespace(
        execute=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    repository = DocumentRepository(db)
    document = SimpleNamespace(id=uuid4())

    with pytest.raises(ValueError, match="SHA-256"):
        await repository.update_markdown_path(
            document,
            "bucket/markdown/document.md",
            "not-a-digest",
        )

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_materialized_document_ids_ignores_processing_status():
    document_ids = [uuid4(), uuid4()]
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _CountResult(2),
                _ScalarsResult(document_ids),
            ]
        )
    )
    repository = DocumentRepository(db)

    ids, total = await repository.list_materialized_document_ids(
        str(uuid4()),
        page=2,
        page_size=25,
    )

    assert ids == [str(doc_id) for doc_id in document_ids]
    assert total == 2
    assert db.execute.await_count == 2
    statements = [str(call.args[0]) for call in db.execute.await_args_list]
    assert all("kb_documents.status" not in statement for statement in statements)
    assert all(
        "kb_documents.markdown_path IS NOT NULL" in statement
        for statement in statements
    )
    assert all("btrim(kb_documents.markdown_path)" in statement for statement in statements)
    assert all(
        "kb_documents.materialization_revision" in statement for statement in statements
    )
