"""Privacy regressions for document chunk editing."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from routes import chunk as chunk_routes
from runtime_state import RagApplicationState, create_request_stats
from schemas import ChunkBatchEditRequest


def _load_chunk_edit_service(monkeypatch, *, embedder_type):
    embedding_package = ModuleType("embedding")
    embedding_package.__path__ = []
    embedding_module = ModuleType("embedding.chunk_embedder")
    embedding_module.ChunkEmbedder = embedder_type
    embedding_module.EmbeddingConfig = lambda **kwargs: SimpleNamespace(**kwargs)

    common_utils_module = ModuleType("common_utils")
    common_utils_module.DeepRAGCommonUtils = type("DeepRAGCommonUtils", (), {})

    monkeypatch.setitem(sys.modules, "embedding", embedding_package)
    monkeypatch.setitem(sys.modules, "embedding.chunk_embedder", embedding_module)
    monkeypatch.setitem(sys.modules, "common_utils", common_utils_module)

    module_path = Path(__file__).resolve().parents[1] / "api" / "chunk_edit_service.py"
    spec = importlib.util.spec_from_file_location("chunk_edit_service_privacy_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_batch_edit_omits_content_and_store_error_details(monkeypatch, caplog):
    private_content_marker = "private-chunk-content-marker"
    private_store_marker = "private-elasticsearch-error-marker"

    class FakeEmbedder:
        def __init__(self, _config):
            pass

        def embed_chunks_sync(self, _chunks):
            return 1, 3

    class FakeRetriever:
        async def close(self):
            return None

    class FakeStore:
        async def store_chunks(self, _chunks, **_kwargs):
            return 0, [{"reason": private_store_marker}]

        async def close(self):
            return None

    class FakeUtils:
        async def create_retriever_async(self, **_kwargs):
            return FakeRetriever()

        def create_document_store(self, **_kwargs):
            return FakeStore()

        async def fetch_chunk_by_id(self, _retriever, chunk_id):
            return {"_id": chunk_id, "available_int": 1}

        def update_chunk_content(self, original_chunk, **_kwargs):
            return dict(original_chunk)

    module = _load_chunk_edit_service(monkeypatch, embedder_type=FakeEmbedder)
    service = module.ChunkEditService.__new__(module.ChunkEditService)
    service.utils = FakeUtils()

    with caplog.at_level(logging.ERROR, logger=module.__name__):
        result = await service.process_batch_chunks_edit(
            chunks=[
                {"content": private_content_marker},
                {"chunk_id": "known-chunk", "content": "safe replacement"},
            ],
            es_host="https://search.example.com",
            index_name="documents",
            model_factory="OpenAI",
            model_name="embedding-model",
        )

    rendered = str(result)
    assert result["success"] is False
    assert private_content_marker not in rendered
    assert private_store_marker not in rendered
    assert private_content_marker not in caplog.text
    assert private_store_marker not in caplog.text
    assert "error_type=StoreResultFailure" in caplog.text


@pytest.mark.asyncio
async def test_batch_edit_route_does_not_trust_service_error_text():
    private_marker = "private-service-result-marker"

    class FakeChunkEditService:
        async def process_batch_chunks_edit(self, **_kwargs):
            return {
                "success": False,
                "message": private_marker,
                "total_chunks": 1,
                "successful_chunks": 0,
                "failed_chunks": 1,
                "errors": [{"chunk_data": {"content": private_marker}}],
            }

    state = RagApplicationState(
        unified_service=SimpleNamespace(
            chunk_edit_service=FakeChunkEditService()
        ),
        stats=create_request_stats(),
    )

    response = await chunk_routes.batch_edit_chunks(
        ChunkBatchEditRequest(
            chunks=[{"chunk_id": "known-chunk", "content": "replacement"}],
            index_name="documents",
            model_factory="OpenAI",
            model_name="embedding-model",
        ),
        state=state,
    )

    assert response.success is False
    assert response.message == "文档块编辑失败"
    assert private_marker not in str(response.model_dump())
