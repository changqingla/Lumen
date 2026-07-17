#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 文档删除路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from error_boundary import log_rag_failure, public_error_message
from runtime_state import RagStateDependency
from schemas import DocumentDeleteRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/delete-document", response_model=UnifiedResponse)
async def delete_document(request: DocumentDeleteRequest, state: RagStateDependency):
    """
    文档删除接口

    根据document_id删除指定索引中该文档的所有分块
    """
    start_time = time.time()
    unified_service = state.unified_service
    stats = state.stats
    stats["total_requests"] += 1

    if not request.document_id.strip():
        stats["failed_requests"] += 1
        raise HTTPException(status_code=400, detail="文档ID不能为空")

    try:
        # 调用文档删除服务
        result = await unified_service.process_document_delete(request)
    except Exception as error:
        stats["failed_requests"] += 1
        log_rag_failure(logger, stage="deleting", error=error)
        raise HTTPException(
            status_code=500,
            detail=public_error_message("deleting"),
        ) from None

    if not result.get("success"):
        stats["failed_requests"] += 1
        raise HTTPException(
            status_code=500,
            detail=public_error_message("deleting"),
        )

    stats["successful_requests"] += 1
    return UnifiedResponse(
        success=True,
        message=result["message"],
        data={
            "deleted_count": result["deleted_count"],
            "document_id": result["document_id"],
            "index_name": result["index_name"],
        },
        processing_time=time.time() - start_time,
        timestamp=datetime.now().isoformat(),
    )
