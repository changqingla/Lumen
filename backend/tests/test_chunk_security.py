import os

os.environ.setdefault("DEBUG", "false")

import sys
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

sys.modules.setdefault("recall_lib", SimpleNamespace(SimpleESConnection=MagicMock()))

from modules.knowledge import chunk_controller
from schemas.chunk_schemas import ChunkEditRequest, ChunkListRequest, ChunkSearchRequest, ChunkBatchEditRequest
from modules.knowledge.services.chunk_service import ChunkService
from utils.es_utils import get_user_es_index


@pytest.mark.asyncio
async def test_chunk_controller_preserves_forbidden_errors(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    request = ChunkListRequest(index_name=get_user_es_index(str(user.id)))

    monkeypatch.setattr(
        chunk_controller,
        "ChunkService",
        MagicMock(list_chunks=AsyncMock(side_effect=HTTPException(status_code=403, detail="forbidden"))),
    )

    with pytest.raises(HTTPException) as exc_info:
        await chunk_controller.list_chunks(request=request, current_user=user)

    assert exc_info.value.status_code == 403


def test_chunk_service_rejects_foreign_index_access():
    owner_id = str(uuid4())
    foreign_index = get_user_es_index(str(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        ChunkService._ensure_user_index_access(foreign_index, owner_id)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_chunk_service_rejects_batch_index_mismatch():
    user_id = str(uuid4())
    index_name = get_user_es_index(user_id)
    request = ChunkBatchEditRequest(
        index_name=index_name,
        chunks=[
            ChunkEditRequest(index_name=index_name, chunk_id="chunk-1", content="ok"),
            ChunkEditRequest(index_name=get_user_es_index(str(uuid4())), chunk_id="chunk-2", content="bad"),
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await ChunkService.batch_edit_chunks(request, user_id)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_chunk_service_allows_own_index_queries(monkeypatch):
    user_id = str(uuid4())
    index_name = get_user_es_index(user_id)
    request = ChunkSearchRequest(index_name=index_name, query="report")
    fake_es = MagicMock()
    fake_es.search = AsyncMock(return_value={"hits": {"total": {"value": 0}, "hits": []}})

    monkeypatch.setattr(ChunkService, "_get_es_conn", AsyncMock(return_value=fake_es))

    result = await ChunkService.search_chunks(request, user_id)

    assert result["total_count"] == 0
    fake_es.search.assert_awaited_once()
