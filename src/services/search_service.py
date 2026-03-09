"""Search service for knowledge base retrieval.

Uses recall_lib directly for vector search instead of HTTP proxy to rag service.
"""
import sys

# recall_lib is mounted at /workspace/recall_lib in Docker
sys.path.insert(0, "/workspace")
sys.path.insert(0, "/workspace/rag")

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from repositories.kb_repository import KnowledgeBaseRepository
from repositories.document_repository import DocumentRepository
from utils.es_utils import get_user_es_index
from config.settings import settings
from typing import List, Dict, Optional
import logging

from recall_lib import (
    DeepRagPureRetriever,
    DeepRagRetrievalConfig,
    create_embedding_model,
    create_rerank_model,
)

logger = logging.getLogger(__name__)

# Lazy-initialized shared instances
_embedding_model = None
_rerank_model = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = create_embedding_model(
            model_factory=settings.EMBEDDING_MODEL_FACTORY,
            model_name=settings.EMBEDDING_MODEL_NAME,
            model_base_url=settings.EMBEDDING_BASE_URL,
            api_key=settings.EMBEDDING_API_KEY,
        )
    return _embedding_model


def _get_rerank_model():
    global _rerank_model
    if _rerank_model is None and settings.RERANK_FACTORY:
        _rerank_model = create_rerank_model(
            rerank_factory=settings.RERANK_FACTORY,
            rerank_model_name=settings.RERANK_MODEL_NAME,
            rerank_base_url=settings.RERANK_BASE_URL,
            rerank_api_key=settings.RERANK_API_KEY,
        )
    return _rerank_model


class SearchService:
    """Service for knowledge base search and retrieval."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.kb_repo = KnowledgeBaseRepository(db)
        self.doc_repo = DocumentRepository(db)
    
    async def search_in_kb(
        self,
        kb_id: str,
        user_id: str,
        question: str,
        top_n: int = 10,
        use_rerank: bool = False
    ) -> Dict:
        """
        Search in knowledge base using recall_lib directly.
        
        Args:
            kb_id: Knowledge base ID
            user_id: User ID (for ownership verification)
            question: User question
            top_n: Number of results to return
            use_rerank: Whether to use reranking model
        
        Returns:
            Search results with chunks and references
        """
        # Verify KB ownership
        kb = await self.kb_repo.get_by_id(kb_id, user_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found"}}
            )
        
        # Get user's ES index name (user-level, shared across all KBs)
        user_es_index = get_user_es_index(user_id)
        
        # Get all document IDs in this KB
        doc_ids = await self.doc_repo.get_all_doc_ids(kb_id)
        
        if not doc_ids:
            return {
                "chunks": [],
                "references": [],
                "message": "No documents in knowledge base"
            }
        
        retriever = None
        try:
            # Create retriever with recall_lib (direct ES access)
            retrieval_config = DeepRagRetrievalConfig(
                index_names=[user_es_index],
                page_size=top_n,
                similarity_threshold=settings.SIMILARITY_THRESHOLD,
                vector_similarity_weight=settings.VECTOR_SIMILARITY_WEIGHT,
                top_k=1024,
                es_config={"hosts": settings.ES_HOST, "timeout": 600},
            )
            retriever = DeepRagPureRetriever(retrieval_config)

            emb_mdl = _get_embedding_model()
            rerank_mdl = _get_rerank_model() if use_rerank else None

            search_result = await retriever.retrieval(
                question=question,
                embd_mdl=emb_mdl,
                page=1,
                page_size=top_n,
                similarity_threshold=settings.SIMILARITY_THRESHOLD,
                vector_similarity_weight=settings.VECTOR_SIMILARITY_WEIGHT,
                top=1024,
                doc_ids=doc_ids,
                rerank_mdl=rerank_mdl,
            )
            
            # Format results
            chunks = search_result.get("chunks", [])
            references = []
            
            for chunk in chunks:
                references.append({
                    "chunkId": chunk.get("chunk_id"),
                    "docId": chunk.get("doc_id"),
                    "docName": chunk.get("docnm_kwd"),
                    "content": chunk.get("content_with_weight", ""),
                    "similarity": chunk.get("similarity", 0),
                    "pageNum": chunk.get("page_num_int", []),
                })
            
            return {
                "chunks": chunks,
                "references": references,
                "total": search_result.get("total", 0)
            }
        
        except Exception as e:
            logger.error(f"Search in KB {kb_id} failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": "INTERNAL_ERROR", "message": f"Search failed: {e}"}}
            )
        finally:
            if retriever:
                try:
                    await retriever.close()
                except Exception:
                    pass

