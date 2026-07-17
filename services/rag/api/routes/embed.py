#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 向量化路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from error_boundary import log_rag_failure, public_error_message
from runtime_state import RagStateDependency
from schemas import EmbeddingRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/embed", response_model=UnifiedResponse)
async def embed_chunks(request: EmbeddingRequest, state: RagStateDependency):
    """
    分块向量化接口

    对已有的分块进行向量化处理
    """
    start_time = time.time()
    unified_service = state.unified_service
    stats = state.stats
    stats["total_requests"] += 1
    stats["embedding_requests"] += 1

    if not request.chunks:
        stats["failed_requests"] += 1
        raise HTTPException(status_code=400, detail="分块列表不能为空")

    if len(request.chunks) > 1000:
        stats["failed_requests"] += 1
        raise HTTPException(status_code=400, detail="单次请求分块数量不能超过 1000")

    try:
        # 调用向量化服务
        result = await unified_service.process_embedding(request.chunks, request)
    except Exception as error:
        stats["failed_requests"] += 1
        log_rag_failure(logger, stage="embedding", error=error)
        raise HTTPException(
            status_code=500,
            detail=public_error_message("embedding"),
        ) from None

    if not result.get("success"):
        stats["failed_requests"] += 1
        raise HTTPException(
            status_code=500,
            detail=public_error_message("embedding"),
        )

    stats["successful_requests"] += 1
    return UnifiedResponse(
        success=True,
        message=f"成功向量化 {len(request.chunks)} 个分块",
        data=result,
        processing_time=time.time() - start_time,
        timestamp=datetime.now().isoformat(),
    )
