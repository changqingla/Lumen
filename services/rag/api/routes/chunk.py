#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 文档块编辑路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from schemas import ChunkEditRequest, ChunkBatchEditRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/edit-chunk", response_model=UnifiedResponse)
async def edit_chunk(request: ChunkEditRequest):
    """
    编辑单个文档块

    支持修改块内容和启用/禁用状态，自动重新分词和向量化
    """
    from app import unified_service, stats

    start_time = time.time()
    stats["total_requests"] += 1

    try:
        result = await unified_service.chunk_edit_service.process_single_chunk_edit(
            chunk_id=request.chunk_id,
            content=request.content,
            available_int=request.available_int,
            es_host=request.es_host,
            index_name=request.index_name,
            model_factory=request.model_factory,
            model_name=request.model_name,
            base_url=request.base_url,
            api_key=request.api_key,
            batch_size=request.batch_size,
            filename_embd_weight=request.filename_embd_weight,
            username=request.username,
            password=request.password,
            timeout=request.timeout,
        )

        processing_time = time.time() - start_time

        if result["success"]:
            stats["successful_requests"] += 1
            return UnifiedResponse(
                success=True,
                message=result["message"],
                data=result.get("data"),
                processing_time=processing_time,
                timestamp=datetime.now().isoformat(),
            )
        else:
            stats["failed_requests"] += 1
            raise HTTPException(status_code=400, detail=result["message"])

    except HTTPException:
        stats["failed_requests"] += 1
        raise
    except Exception as e:
        stats["failed_requests"] += 1
        logger.error(f"编辑块失败: {e}")
        raise HTTPException(status_code=500, detail=f"编辑块失败: {str(e)}")


@router.post("/api/batch-edit-chunks", response_model=UnifiedResponse)
async def batch_edit_chunks(request: ChunkBatchEditRequest):
    """
    批量编辑文档块

    支持批量修改块内容和启用/禁用状态，自动重新分词和向量化
    """
    from app import unified_service, stats

    start_time = time.time()
    stats["total_requests"] += 1

    try:
        result = await unified_service.chunk_edit_service.process_batch_chunks_edit(
            chunks=request.chunks,
            es_host=request.es_host,
            index_name=request.index_name,
            model_factory=request.model_factory,
            model_name=request.model_name,
            base_url=request.base_url,
            api_key=request.api_key,
            batch_size=request.batch_size,
            filename_embd_weight=request.filename_embd_weight,
            username=request.username,
            password=request.password,
            timeout=request.timeout,
        )

        processing_time = time.time() - start_time

        if result["success"]:
            stats["successful_requests"] += 1
        else:
            stats["failed_requests"] += 1

        return UnifiedResponse(
            success=result["success"],
            message=result["message"],
            data={
                "total_chunks": result.get("total_chunks", 0),
                "successful_chunks": result.get("successful_chunks", 0),
                "failed_chunks": result.get("failed_chunks", 0),
                "errors": result.get("errors", []),
            },
            processing_time=processing_time,
            timestamp=datetime.now().isoformat(),
        )

    except HTTPException:
        stats["failed_requests"] += 1
        raise
    except Exception as e:
        stats["failed_requests"] += 1
        logger.error(f"批量编辑块失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量编辑块失败: {str(e)}")
