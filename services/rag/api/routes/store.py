#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 存储路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from schemas import StoreRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/store", response_model=UnifiedResponse)
async def store_chunks(request: StoreRequest):
    """
    分块存储接口

    将向量化后的分块存储到Elasticsearch
    """
    from app import unified_service, stats

    start_time = time.time()
    stats["total_requests"] += 1
    stats["store_requests"] += 1

    try:
        if not request.chunks:
            raise HTTPException(status_code=400, detail="分块列表不能为空")

        # 调用存储服务
        result = await unified_service.process_store(request.chunks, request)

        processing_time = time.time() - start_time

        if result["success"]:
            stats["successful_requests"] += 1
            return UnifiedResponse(
                success=True,
                message=f"成功存储 {result.get('stored_count', 0)} 个分块",
                data=result,
                processing_time=processing_time,
                timestamp=datetime.now().isoformat()
            )
        else:
            stats["failed_requests"] += 1
            raise HTTPException(status_code=500, detail=result.get("message", "存储处理失败"))

    except HTTPException:
        stats["failed_requests"] += 1
        raise
    except Exception as e:
        stats["failed_requests"] += 1
        logger.error(f"存储处理失败: {e}")
        raise HTTPException(status_code=500, detail=f"存储处理失败: {str(e)}")
