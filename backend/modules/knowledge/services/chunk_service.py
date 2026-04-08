"""Chunk management service using recall_lib's SimpleESConnection for ES operations."""
import sys
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import HTTPException, status

from config.settings import settings
from schemas.chunk_schemas import (
    ChunkListRequest,
    ChunkSearchRequest,
    ChunkEditRequest,
    ChunkBatchEditRequest,
)
from utils.external_services import get_http_client
from utils.es_utils import get_user_es_index

logger = logging.getLogger(__name__)
_SimpleESConnection = None


def _ensure_recall_lib_path() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    candidate_paths = [
        repo_root / "shared" / "python",
        Path("/app/shared/python"),
        Path("/workspace/shared/python"),
        Path("/workspace/rag"),
    ]
    for path in candidate_paths:
        normalized = str(path)
        if path.exists() and normalized not in sys.path:
            sys.path.insert(0, normalized)


def _get_simple_es_connection_class():
    global _SimpleESConnection

    if _SimpleESConnection is not None:
        return _SimpleESConnection

    _ensure_recall_lib_path()

    from recall_lib import SimpleESConnection

    _SimpleESConnection = SimpleESConnection
    return _SimpleESConnection

# Source fields to retrieve from ES for chunk queries
_CHUNK_SOURCE_FIELDS = [
    "chunk_id", "doc_id", "docnm_kwd", "content_with_weight",
    "title_tks", "page_num_int", "position_int", "available_int",
    "create_time", "chunk_index",
]


def _format_chunk(hit: Dict[str, Any], include_score: bool = False) -> Dict[str, Any]:
    """Format a single ES hit into a chunk response dict."""
    src = hit["_source"]
    chunk = {
        "chunk_id": src.get("chunk_id", hit["_id"]),
        "document_id": src.get("doc_id", ""),
        "document_name": src.get("docnm_kwd", ""),
        "content": src.get("content_with_weight", ""),
        "title": src.get("title_tks", ""),
        "page_number": src.get("page_num_int", 0),
        "position": src.get("position_int", 0),
        "chunk_index": src.get("chunk_index", 0),
        "available": src.get("available_int", 1),
        "create_time": src.get("create_time", ""),
    }
    if include_score:
        chunk["score"] = hit.get("_score")
    return chunk


class ChunkService:
    """Service for chunk CRUD operations.

    Uses recall_lib SimpleESConnection directly for list/search queries.
    Proxies edit operations to the rag service (which handles re-embedding).
    """

    _es_conn: Optional[Any] = None

    @classmethod
    async def _get_es_conn(cls):
        """Get or create a shared SimpleESConnection instance."""
        if cls._es_conn is None:
            simple_es_connection = _get_simple_es_connection_class()
            cls._es_conn = simple_es_connection(hosts=settings.ES_HOST)
        await cls._es_conn.ensure_connected()
        return cls._es_conn

    @staticmethod
    def _ensure_user_index_access(index_name: str, user_id: str) -> None:
        """Only allow callers to operate on their own ES index."""
        expected_index = get_user_es_index(user_id)
        if index_name != expected_index:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "You can only access chunks in your own knowledge index",
                    }
                },
            )

    @classmethod
    async def list_chunks(cls, request: ChunkListRequest, user_id: str) -> Dict[str, Any]:
        """List chunks for a document with pagination.

        Queries ES directly via SimpleESConnection.
        """
        cls._ensure_user_index_access(request.index_name, user_id)
        es = await cls._get_es_conn()
        from_offset = (request.page - 1) * request.page_size

        query: Dict[str, Any] = {
            "_source": _CHUNK_SOURCE_FIELDS,
            "from": from_offset,
            "size": request.page_size,
            "sort": [
                {"chunk_index": {"order": "asc"}},
                {"position_int": {"order": "asc"}},
            ],
        }

        # If doc_id is provided, filter by it; otherwise return all chunks in the index
        if request.doc_id:
            query["query"] = {"term": {"doc_id": request.doc_id}}
        else:
            query["query"] = {"match_all": {}}

        result = await es.search(
            index_name=request.index_name,
            query=query,
            size=request.page_size,
        )

        hits = result.get("hits", {})
        total_count = hits.get("total", {}).get("value", 0)
        chunks = [_format_chunk(hit) for hit in hits.get("hits", [])]

        return {
            "chunks": chunks,
            "total_count": total_count,
            "page": request.page,
            "page_size": request.page_size,
            "total_pages": (total_count + request.page_size - 1) // request.page_size,
        }

    @classmethod
    async def search_chunks(cls, request: ChunkSearchRequest, user_id: str) -> Dict[str, Any]:
        """Search chunks by keyword with pagination and highlight.

        Queries ES directly via SimpleESConnection.
        """
        cls._ensure_user_index_access(request.index_name, user_id)
        es = await cls._get_es_conn()
        from_offset = (request.page - 1) * request.page_size

        # Build bool query
        must_clauses = [
            {
                "multi_match": {
                    "query": request.query,
                    "fields": ["content_with_weight^2", "title_tks^1.5"],
                    "type": "best_fields",
                    "operator": "and",
                }
            }
        ]

        # Filter by doc_ids if provided
        if request.doc_ids:
            must_clauses.insert(0, {"terms": {"doc_id": request.doc_ids}})

        query: Dict[str, Any] = {
            "query": {"bool": {"must": must_clauses}},
            "_source": _CHUNK_SOURCE_FIELDS,
            "from": from_offset,
            "size": request.page_size,
            "sort": [
                {"_score": {"order": "desc"}},
                {"chunk_index": {"order": "asc"}},
                {"position_int": {"order": "asc"}},
            ],
            "highlight": {
                "fields": {
                    "content_with_weight": {
                        "pre_tags": ["<mark>"],
                        "post_tags": ["</mark>"],
                        "fragment_size": 150,
                        "number_of_fragments": 3,
                    },
                    "title_tks": {
                        "pre_tags": ["<mark>"],
                        "post_tags": ["</mark>"],
                    },
                }
            },
        }

        result = await es.search(
            index_name=request.index_name,
            query=query,
            size=request.page_size,
        )

        hits = result.get("hits", {})
        total_count = hits.get("total", {}).get("value", 0)

        chunks = []
        for hit in hits.get("hits", []):
            chunk = _format_chunk(hit, include_score=True)
            # Attach highlight info if present
            if "highlight" in hit:
                highlight_info = {}
                if "content_with_weight" in hit["highlight"]:
                    highlight_info["content"] = hit["highlight"]["content_with_weight"]
                if "title_tks" in hit["highlight"]:
                    highlight_info["title"] = hit["highlight"]["title_tks"]
                chunk["highlight"] = highlight_info
            chunks.append(chunk)

        return {
            "chunks": chunks,
            "total_count": total_count,
            "page": request.page,
            "page_size": request.page_size,
            "total_pages": (total_count + request.page_size - 1) // request.page_size,
            "search_query": request.query,
        }

    @classmethod
    async def edit_chunk(cls, request: ChunkEditRequest, user_id: str) -> Dict[str, Any]:
        """Edit a single chunk by proxying to the rag service.

        The rag service handles re-embedding which requires ChunkEmbedder.
        """
        cls._ensure_user_index_access(request.index_name, user_id)
        payload = {
            "chunk_id": request.chunk_id,
            "es_host": settings.ES_HOST,
            "index_name": request.index_name,
            "content": request.content,
            "available_int": request.available_int if request.available_int is not None else 1,
            "model_factory": settings.EMBEDDING_MODEL_FACTORY,
            "model_name": settings.EMBEDDING_MODEL_NAME,
            "base_url": settings.EMBEDDING_BASE_URL,
            "api_key": settings.EMBEDDING_API_KEY or None,
        }

        response = await get_http_client().post(
            f"{settings.DOC_PROCESS_BASE_URL}/edit-chunk",
            json=payload,
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("success"):
            raise Exception(result.get("message", "Chunk edit failed"))

        return result.get("data", {})

    @classmethod
    async def batch_edit_chunks(cls, request: ChunkBatchEditRequest, user_id: str) -> Dict[str, Any]:
        """Batch edit chunks by proxying to the rag service.

        The rag service handles re-embedding which requires ChunkEmbedder.
        """
        cls._ensure_user_index_access(request.index_name, user_id)
        chunks_payload = []
        for chunk in request.chunks:
            if chunk.index_name != request.index_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": "INVALID_REQUEST",
                            "message": "All chunks in a batch edit must target the same index",
                        }
                    },
                )
            chunks_payload.append({
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "available_int": chunk.available_int if chunk.available_int is not None else 1,
            })

        payload = {
            "chunks": chunks_payload,
            "es_host": settings.ES_HOST,
            "index_name": request.index_name,
            "model_factory": settings.EMBEDDING_MODEL_FACTORY,
            "model_name": settings.EMBEDDING_MODEL_NAME,
            "base_url": settings.EMBEDDING_BASE_URL,
            "api_key": settings.EMBEDDING_API_KEY or None,
        }

        response = await get_http_client().post(
            f"{settings.DOC_PROCESS_BASE_URL}/batch-edit-chunks",
            json=payload,
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("success"):
            raise Exception(result.get("message", "Batch edit failed"))

        return result.get("data", {})

    @classmethod
    async def close(cls) -> None:
        """Close the shared ES connection."""
        if cls._es_conn is not None:
            await cls._es_conn.close()
            cls._es_conn = None
