import os

os.environ["DEBUG"] = "false"

import importlib
import sys
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

sys.modules.setdefault("recall_lib", SimpleNamespace(SimpleESConnection=MagicMock()))

chunk_controller = importlib.import_module("modules.knowledge.chunk_controller")
chunk_schemas = importlib.import_module("schemas.chunk_schemas")
ChunkEditRequest = chunk_schemas.ChunkEditRequest
ChunkListRequest = chunk_schemas.ChunkListRequest
ChunkSearchRequest = chunk_schemas.ChunkSearchRequest
ChunkBatchEditRequest = chunk_schemas.ChunkBatchEditRequest
ChunkService = importlib.import_module(
    "modules.knowledge.services.chunk_service"
).ChunkService
get_user_es_index = importlib.import_module("utils.es_utils").get_user_es_index


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


@pytest.mark.asyncio
async def test_chunk_controller_redacts_internal_failures(monkeypatch, caplog):
    marker = "private-elasticsearch-provider-detail"
    user = SimpleNamespace(id=uuid4())
    request = ChunkListRequest(index_name=get_user_es_index(str(user.id)))
    monkeypatch.setattr(
        chunk_controller,
        "ChunkService",
        MagicMock(list_chunks=AsyncMock(side_effect=RuntimeError(marker))),
    )

    with pytest.raises(HTTPException) as exc_info:
        await chunk_controller.list_chunks(request=request, current_user=user)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Chunk operation failed"
    assert marker not in str(exc_info.value.detail)
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


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
