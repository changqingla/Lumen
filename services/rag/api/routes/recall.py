#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 混合检索召回路由
"""

import logging
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException

from common_utils import DeepRAGCommonUtils
from error_boundary import log_rag_failure, public_error_message
from recall_lib import create_embedding_model, create_rerank_model
from schemas import RecallRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/hybrid-recall", response_model=UnifiedResponse)
async def hybrid_recall(
    request: RecallRequest,
):
    """
    混合检索召回接口（文本 + 向量）。

    说明：
    - 仅负责召回，不负责答案生成
    - doc_ids 由调用方决定过滤范围（Agent 侧会基于 kb_docs 白名单传入）
    """
    start_time = time.time()
    retriever = None

    question = (request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")

    if not request.index_names:
        raise HTTPException(status_code=400, detail="index_names 不能为空")

    if not request.doc_ids:
        raise HTTPException(status_code=400, detail="doc_ids 不能为空")

    try:
        emb_model = create_embedding_model(
            model_factory=request.model_factory,
            model_name=request.model_name,
            model_base_url=request.model_base_url,
            api_key=request.api_key,
        )

        rerank_model = None
        if request.rerank_factory:
            rerank_model = create_rerank_model(
                rerank_factory=request.rerank_factory,
                rerank_model_name=request.rerank_model_name or "",
                rerank_base_url=request.rerank_base_url,
                rerank_api_key=request.rerank_api_key,
            )

        retriever = await DeepRAGCommonUtils.create_retriever_async(
            es_host=request.es_host,
            index_names=request.index_names,
            page=request.page,
            page_size=request.top_n,
            similarity_threshold=request.similarity_threshold,
            vector_similarity_weight=request.vector_similarity_weight,
            highlight=request.highlight,
            timeout=request.timeout,
        )

        search_result = await retriever.retrieval(
            question=question,
            embd_mdl=emb_model,
            page=request.page,
            page_size=request.top_n,
            similarity_threshold=request.similarity_threshold,
            vector_similarity_weight=request.vector_similarity_weight,
            top=request.top_k,
            doc_ids=request.doc_ids,
            rerank_mdl=rerank_model,
            highlight=request.highlight,
        )

        chunks = search_result.get("chunks", []) or []
        processing_time = time.time() - start_time

        return UnifiedResponse(
            success=True,
            message=f"召回完成，返回 {len(chunks)} 个文档块",
            data={
                "total": search_result.get("total", 0),
                "chunks": chunks,
                "doc_aggs": search_result.get("doc_aggs", {}),
                "index_names": request.index_names,
                "doc_ids": request.doc_ids,
                "page": request.page,
                "top_n": request.top_n,
                "top_k": request.top_k,
                "similarity_threshold": request.similarity_threshold,
                "vector_similarity_weight": request.vector_similarity_weight,
                "used_rerank": bool(rerank_model is not None),
            },
            processing_time=processing_time,
            timestamp=datetime.now().isoformat(),
        )
    except Exception as error:
        log_rag_failure(logger, stage="recall", error=error)
        raise HTTPException(
            status_code=500,
            detail=public_error_message("recall"),
        ) from None
    finally:
        if retriever is not None:
            try:
                await retriever.close()
            except Exception as error:
                log_rag_failure(
                    logger,
                    stage="retriever_cleanup",
                    error=error,
                )
