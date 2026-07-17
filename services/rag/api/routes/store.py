#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 存储路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from error_boundary import log_rag_failure, public_error_message
from runtime_state import RagStateDependency
from schemas import StoreRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/store", response_model=UnifiedResponse)
async def store_chunks(request: StoreRequest, state: RagStateDependency):
    """
    分块存储接口

    将向量化后的分块存储到Elasticsearch
    """
    start_time = time.time()
    unified_service = state.unified_service
    stats = state.stats
    stats["total_requests"] += 1
    stats["store_requests"] += 1

    if not request.chunks:
        stats["failed_requests"] += 1
        raise HTTPException(status_code=400, detail="分块列表不能为空")

    try:
        # 调用存储服务
        result = await unified_service.process_store(request.chunks, request)
    except Exception as error:
        stats["failed_requests"] += 1
        log_rag_failure(logger, stage="storing", error=error)
        raise HTTPException(
            status_code=500,
            detail=public_error_message("storing"),
        ) from None

    if not result.get("success"):
        stats["failed_requests"] += 1
        raise HTTPException(
            status_code=500,
            detail=public_error_message("storing"),
        )

    stats["successful_requests"] += 1
    return UnifiedResponse(
        success=True,
        message=f"成功存储 {result.get('stored_count', 0)} 个分块",
        data=result,
        processing_time=time.time() - start_time,
        timestamp=datetime.now().isoformat(),
    )
