#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 向量化路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from schemas import EmbeddingRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/embed", response_model=UnifiedResponse)
async def embed_chunks(request: EmbeddingRequest):
    """
    分块向量化接口

    对已有的分块进行向量化处理
    """
    from app import unified_service, stats

    start_time = time.time()
    stats["total_requests"] += 1
    stats["embedding_requests"] += 1

    try:
        if not request.chunks:
            raise HTTPException(status_code=400, detail="分块列表不能为空")

        if len(request.chunks) > 1000:
            raise HTTPException(status_code=400, detail="单次请求分块数量不能超过 1000")

        # 调用向量化服务
        result = await unified_service.process_embedding(request.chunks, request)

        processing_time = time.time() - start_time

        if result["success"]:
            stats["successful_requests"] += 1
            return UnifiedResponse(
                success=True,
                message=f"成功向量化 {len(request.chunks)} 个分块",
                data=result,
                processing_time=processing_time,
                timestamp=datetime.now().isoformat()
            )
        else:
            stats["failed_requests"] += 1
            raise HTTPException(status_code=500, detail=result.get("message", "向量化处理失败"))

    except HTTPException:
        stats["failed_requests"] += 1
        raise
    except Exception as e:
        stats["failed_requests"] += 1
        logger.error(f"向量化处理失败: {e}")
        raise HTTPException(status_code=500, detail=f"向量化处理失败: {str(e)}")
