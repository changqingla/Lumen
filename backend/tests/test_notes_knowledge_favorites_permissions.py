import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from modules.favorites.repositories.favorite_repository import FavoriteRepository
from modules.favorites.services.favorite_service import FavoriteService
from modules.knowledge.repositories.kb_repository import KnowledgeBaseRepository
from modules.knowledge.repositories.kb_subscription_repository import (
    KBSubscriptionRepository,
)
from modules.knowledge.services.document_service import DocumentService
from modules.knowledge.services.kb_service import KnowledgeBaseService
from modules.notes.services.note_service import NoteService


class _EmptyResult:
    def scalar(self):
        return 0

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _RecordingDB:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _EmptyResult()


def _compiled_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _where_clause(statement) -> str:
    return _compiled_sql(statement).split("WHERE", 1)[1]


def _dict_model(**values):
    model = SimpleNamespace(**values)
    model.to_dict = lambda **_kwargs: dict(values)
    return model


@pytest.mark.asyncio
async def test_create_note_rejects_folder_owned_by_another_user():
    service = NoteService(db=object())
    service.folder_repo.get_by_id = AsyncMock(return_value=None)
    service.note_repo.create = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await service.create_note(
            str(uuid4()),
            "title",
            "content",
            str(uuid4()),
            [],
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "NOT_FOUND"
    service.note_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_note_rejects_move_to_foreign_folder():
    service = NoteService(db=object())
    service.note_repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    service.folder_repo.get_by_id = AsyncMock(return_value=None)
    service.note_repo.update = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await service.update_note(
            str(uuid4()),
            str(uuid4()),
            folderId=str(uuid4()),
        )

    assert exc_info.value.status_code == 404
    service.note_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_note_allows_explicit_move_to_unfiled_collection():
    service = NoteService(db=object())
    note = SimpleNamespace(id=uuid4())
    updated = _dict_model(id=str(note.id), folder="未分类")
    service.note_repo.get_by_id = AsyncMock(return_value=note)
    service.folder_repo.get_by_id = AsyncMock()
    service.note_repo.update = AsyncMock(return_value=updated)

    result = await service.update_note(str(note.id), str(uuid4()), folderId=None)

    assert result["folder"] == "未分类"
    service.folder_repo.get_by_id.assert_not_awaited()
    service.note_repo.update.assert_awaited_once_with(note, folder_id=None)


@pytest.mark.asyncio
async def test_repository_public_visibility_is_one_atomic_update():
    kb = SimpleNamespace(visibility="organization", shared_to_orgs=[uuid4()])
    db = SimpleNamespace(
        get=AsyncMock(return_value=kb),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    repository = KnowledgeBaseRepository(db)

    result = await repository.update_visibility(
        uuid4(),
        "public",
        is_admin=True,
    )

    assert result is kb
    assert kb.visibility == "public"
    assert kb.shared_to_orgs == []
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_accessible_kb_query_encodes_owner_public_and_organization_rules():
    db = _RecordingDB()
    repository = KnowledgeBaseRepository(db)

    await repository.get_accessible_by_id(
        uuid4(),
        uuid4(),
        [uuid4()],
    )

    sql = _compiled_sql(db.statements[0])
    assert "knowledge_bases.id" in sql
    assert "knowledge_bases.owner_id" in sql
    assert "knowledge_bases.visibility" in sql
    assert "knowledge_bases.shared_to_orgs &&" in sql


@pytest.mark.asyncio
async def test_admin_accessible_kb_query_does_not_apply_visibility_rules():
    db = _RecordingDB()
    repository = KnowledgeBaseRepository(db)

    await repository.get_accessible_by_id(uuid4(), uuid4(), is_admin=True)

    where_clause = _where_clause(db.statements[0])
    assert "knowledge_bases.id" in where_clause
    assert "knowledge_bases.owner_id" not in where_clause
    assert "knowledge_bases.visibility" not in where_clause


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [False, True])
async def test_writable_kb_query_allows_only_owner_or_admin(is_admin):
    db = _RecordingDB()
    repository = KnowledgeBaseRepository(db)

    await repository.get_writable_by_id(
        uuid4(),
        uuid4(),
        is_admin=is_admin,
    )

    where_clause = _where_clause(db.statements[0])
    assert "knowledge_bases.id" in where_clause
    assert ("knowledge_bases.owner_id" in where_clause) is (not is_admin)
    assert "knowledge_bases.visibility" not in where_clause


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_admin", "is_owner"),
    [(False, True), (True, False)],
    ids=["owner", "admin"],
)
async def test_document_owner_and_admin_have_read_and_write_access(
    is_admin,
    is_owner,
):
    service = DocumentService(db=object())
    user_id = uuid4()
    kb_id = uuid4()
    kb = SimpleNamespace(
        id=kb_id,
        owner_id=user_id if is_owner else uuid4(),
    )
    service.user_repo.get_by_id = AsyncMock(
        return_value=SimpleNamespace(is_admin=is_admin)
    )
    service.org_member_repo.get_user_org_ids = AsyncMock(return_value=[])
    service.kb_repo.get_accessible_by_id = AsyncMock(return_value=kb)
    service.kb_repo.get_writable_by_id = AsyncMock(return_value=kb)

    assert await service._verify_kb_access(str(kb_id), str(user_id)) is kb
    assert await service._verify_kb_write_access(str(kb_id), str(user_id)) is kb

    service.kb_repo.get_accessible_by_id.assert_awaited_once_with(
        str(kb_id),
        user_id,
        [],
        is_admin=is_admin,
    )
    service.kb_repo.get_writable_by_id.assert_awaited_once_with(
        str(kb_id),
        str(user_id),
        is_admin=is_admin,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["public", "organization"])
async def test_document_public_and_organization_readers_cannot_write(visibility):
    service = DocumentService(db=object())
    user_id = uuid4()
    kb_id = uuid4()
    service.user_repo.get_by_id = AsyncMock(
        return_value=SimpleNamespace(is_admin=False)
    )
    service.kb_repo.get_writable_by_id = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await service._verify_kb_write_access(str(kb_id), str(user_id))

    assert exc_info.value.status_code == 403, visibility
    assert exc_info.value.detail["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_document_unrelated_user_read_is_not_found():
    service = DocumentService(db=object())
    user_id = uuid4()
    kb_id = uuid4()
    service.user_repo.get_by_id = AsyncMock(
        return_value=SimpleNamespace(is_admin=False)
    )
    service.org_member_repo.get_user_org_ids = AsyncMock(return_value=[])
    service.kb_repo.get_accessible_by_id = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await service._verify_kb_access(str(kb_id), str(user_id))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_kb_info_uses_canonical_access_query_and_preserves_not_found():
    service = KnowledgeBaseService(db=object())
    user_id = uuid4()
    org_id = uuid4()
    service._is_admin = AsyncMock(return_value=False)
    service.org_member_repo.get_user_org_ids = AsyncMock(return_value=[org_id])
    service.kb_repo.get_accessible_by_id = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc_info:
        await service.get_kb_info(str(uuid4()), str(user_id))

    assert exc_info.value.status_code == 404
    service.kb_repo.get_accessible_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_document_read_access_uses_canonical_access_query():
    service = DocumentService(db=object())
    user_id = uuid4()
    org_id = uuid4()
    kb = SimpleNamespace(id=uuid4())
    service.user_repo.get_by_id = AsyncMock(
        return_value=SimpleNamespace(is_admin=False)
    )
    service.org_member_repo.get_user_org_ids = AsyncMock(return_value=[org_id])
    service.kb_repo.get_accessible_by_id = AsyncMock(return_value=kb)

    result = await service._verify_kb_access(str(kb.id), str(user_id))

    assert result is kb
    service.kb_repo.get_accessible_by_id.assert_awaited_once_with(
        str(kb.id),
        user_id,
        [org_id],
        is_admin=False,
    )


@pytest.mark.asyncio
async def test_favorite_access_uses_canonical_access_query():
    service = FavoriteService(db=object())
    user_id = uuid4()
    org_id = uuid4()
    kb = SimpleNamespace(id=uuid4())
    service._get_access_context = AsyncMock(
        return_value=(user_id, [org_id], False)
    )
    service.kb_repo.get_accessible_by_id = AsyncMock(return_value=kb)

    result = await service._check_kb_access(str(kb.id), str(user_id))

    assert result is kb
    service.kb_repo.get_accessible_by_id.assert_awaited_once_with(
        str(kb.id),
        user_id,
        [org_id],
        is_admin=False,
    )


@pytest.mark.asyncio
async def test_admin_visibility_update_keeps_kb_public():
    service = KnowledgeBaseService(db=object())
    kb_id = uuid4()
    user_id = uuid4()
    current = SimpleNamespace(visibility="private", shared_to_orgs=[])
    updated = _dict_model(visibility="public", shared_to_orgs=[])
    service._verify_kb_write_access = AsyncMock(return_value=current)
    service._is_admin = AsyncMock(return_value=True)
    service.kb_repo.update_visibility = AsyncMock(return_value=updated)

    result = await service.update_visibility(
        str(kb_id),
        str(user_id),
        "public",
    )

    assert result["visibility"] == "public"
    service.kb_repo.update_visibility.assert_awaited_once_with(
        kb_id,
        "public",
        is_admin=True,
        shared_to_orgs=None,
    )


@pytest.mark.asyncio
async def test_legacy_toggle_cannot_publish_for_non_admin():
    service = KnowledgeBaseService(db=object())
    kb_id = uuid4()
    user_id = uuid4()
    service._verify_kb_write_access = AsyncMock(
        return_value=SimpleNamespace(visibility="private")
    )
    service._is_admin = AsyncMock(return_value=False)
    service.kb_repo.update_visibility = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await service.toggle_public(str(kb_id), str(user_id))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["code"] == "FORBIDDEN"
    service.kb_repo.update_visibility.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_toggle_allows_owner_to_make_public_kb_private():
    service = KnowledgeBaseService(db=object())
    kb_id = uuid4()
    user_id = uuid4()
    current = SimpleNamespace(visibility="public")
    updated = SimpleNamespace(
        visibility="private",
        subscribers_count=3,
    )
    service._verify_kb_write_access = AsyncMock(return_value=current)
    service._is_admin = AsyncMock(return_value=False)
    service.kb_repo.update_visibility = AsyncMock(return_value=updated)

    result = await service.toggle_public(str(kb_id), str(user_id))

    assert result == {"visibility": "private", "subscribersCount": 3}
    service.kb_repo.update_visibility.assert_awaited_once_with(
        kb_id,
        "private",
        is_admin=False,
    )


@pytest.mark.asyncio
async def test_update_organization_visibility_replaces_validated_org_list():
    service = KnowledgeBaseService(db=object())
    kb_id = uuid4()
    user_id = uuid4()
    org_id = uuid4()
    current = SimpleNamespace(visibility="private", shared_to_orgs=[])
    updated = _dict_model(
        visibility="organization",
        shared_to_orgs=[org_id],
    )
    service._verify_kb_write_access = AsyncMock(return_value=current)
    service._is_admin = AsyncMock(return_value=False)
    service.org_member_repo.get_user_org_ids = AsyncMock(return_value=[org_id])
    service.kb_repo.update_visibility = AsyncMock(return_value=updated)

    result = await service.update_visibility(
        str(kb_id),
        str(user_id),
        "organization",
        [str(org_id), str(org_id)],
    )

    assert result["visibility"] == "organization"
    service.kb_repo.update_visibility.assert_awaited_once_with(
        kb_id,
        "organization",
        is_admin=False,
        shared_to_orgs=[org_id],
    )


@pytest.mark.asyncio
async def test_organization_visibility_requires_at_least_one_org():
    service = KnowledgeBaseService(db=object())
    service._verify_kb_write_access = AsyncMock(
        return_value=SimpleNamespace(visibility="private", shared_to_orgs=[])
    )
    service._is_admin = AsyncMock(return_value=False)
    service.kb_repo.update_visibility = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await service.update_visibility(
            str(uuid4()),
            str(uuid4()),
            "organization",
            [],
        )

    assert exc_info.value.status_code == 400
    service.kb_repo.update_visibility.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type", ["knowledge_base", "document"])
async def test_favorite_queries_filter_access_before_count_and_pagination(item_type):
    db = _RecordingDB()
    repository = FavoriteRepository(db)
    user_id = uuid4()
    org_id = uuid4()

    if item_type == "knowledge_base":
        await repository.list_kb_favorites(
            str(user_id),
            user_org_ids=[org_id],
        )
    else:
        await repository.list_doc_favorites(
            str(user_id),
            user_org_ids=[org_id],
        )

    assert len(db.statements) == 2
    for statement in db.statements:
        sql = _compiled_sql(statement)
        assert "knowledge_bases.owner_id" in sql
        assert "knowledge_bases.visibility" in sql
        assert "knowledge_bases.shared_to_orgs &&" in sql

    db.statements.clear()
    if item_type == "knowledge_base":
        await repository.list_kb_favorites(str(user_id), user_org_ids=[])
    else:
        await repository.list_doc_favorites(str(user_id), user_org_ids=[])

    assert len(db.statements) == 2
    count_sql, page_sql = (_compiled_sql(statement) for statement in db.statements)
    for sql in (count_sql, page_sql):
        where_clause = sql.split("WHERE", 1)[1]
        assert "knowledge_bases.owner_id" in where_clause
        assert "knowledge_bases.visibility" in where_clause
        assert "knowledge_bases.shared_to_orgs &&" not in where_clause
    assert "LIMIT" not in count_sql
    assert "LIMIT" in page_sql


@pytest.mark.asyncio
async def test_subscription_query_filters_access_before_count_and_pagination():
    db = _RecordingDB()
    repository = KBSubscriptionRepository(db)

    await repository.list_user_subscriptions(
        str(uuid4()),
        user_org_ids=[uuid4()],
    )

    assert len(db.statements) == 2
    for statement in db.statements:
        sql = _compiled_sql(statement)
        assert "knowledge_bases.owner_id" in sql
        assert "knowledge_bases.visibility" in sql
        assert "knowledge_bases.shared_to_orgs &&" in sql


@pytest.mark.asyncio
async def test_favorite_service_passes_current_access_context_to_repository():
    service = FavoriteService(db=object())
    user_id = uuid4()
    org_id = uuid4()
    service._get_access_context = AsyncMock(
        return_value=(user_id, [org_id], False)
    )
    service.favorite_repo.list_kb_favorites = AsyncMock(return_value=([], 0))

    assert await service.list_favorite_kbs(str(user_id)) == ([], 0)
    service.favorite_repo.list_kb_favorites.assert_awaited_once_with(
        str(user_id),
        1,
        20,
        user_org_ids=[org_id],
        is_admin=False,
    )


@pytest.mark.asyncio
async def test_subscription_service_passes_current_access_context_to_repository():
    service = KnowledgeBaseService(db=object())
    user_id = uuid4()
    org_id = uuid4()
    service.user_repo.get_by_id = AsyncMock(
        return_value=SimpleNamespace(is_admin=False)
    )
    service.org_member_repo.get_user_org_ids = AsyncMock(return_value=[org_id])
    service.subscription_repo.list_user_subscriptions = AsyncMock(
        return_value=([], 0)
    )

    assert await service.list_user_subscriptions(str(user_id)) == ([], 0)
    service.subscription_repo.list_user_subscriptions.assert_awaited_once_with(
        str(user_id),
        1,
        20,
        user_org_ids=[org_id],
        is_admin=False,
    )
