#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 文档删除路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from schemas import DocumentDeleteRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/delete-document", response_model=UnifiedResponse)
async def delete_document(request: DocumentDeleteRequest):
    """
    文档删除接口

    根据document_id删除指定索引中该文档的所有分块
    """
    from app import unified_service, stats

    start_time = time.time()
    stats["total_requests"] += 1

    try:
        if not request.document_id.strip():
            raise HTTPException(status_code=400, detail="文档ID不能为空")
        
        # 调用文档删除服务
        result = await unified_service.process_document_delete(request)

        processing_time = time.time() - start_time

        if result["success"]:
            stats["successful_requests"] += 1
            return UnifiedResponse(
                success=True,
                message=result["message"],
                data={
                    "deleted_count": result["deleted_count"],
                    "document_id": result["document_id"],
                    "index_name": result["index_name"]
                },
                processing_time=processing_time,
                timestamp=datetime.now().isoformat()
            )
        else:
            stats["failed_requests"] += 1
            raise HTTPException(status_code=500, detail=result.get("message", "文档删除失败"))

    except HTTPException:
        stats["failed_requests"] += 1
        raise
    except Exception as e:
        stats["failed_requests"] += 1
        logger.error(f"文档删除API失败: {e}")
        raise HTTPException(status_code=500, detail=f"文档删除失败: {str(e)}")
