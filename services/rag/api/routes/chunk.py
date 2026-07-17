#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 文档块编辑路由
"""

import time
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from error_boundary import log_rag_failure, public_error_message
from runtime_state import RagStateDependency
from schemas import ChunkEditRequest, ChunkBatchEditRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/edit-chunk", response_model=UnifiedResponse)
async def edit_chunk(request: ChunkEditRequest, state: RagStateDependency):
    """
    编辑单个文档块

    支持修改块内容和启用/禁用状态，自动重新分词和向量化
    """
    start_time = time.time()
    unified_service = state.unified_service
    stats = state.stats
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

    except Exception as error:
        stats["failed_requests"] += 1
        log_rag_failure(logger, stage="chunk_edit", error=error)
        raise HTTPException(
            status_code=500,
            detail=public_error_message("chunk_edit"),
        ) from None

    if not result.get("success"):
        stats["failed_requests"] += 1
        raise HTTPException(
            status_code=400,
            detail=public_error_message("chunk_edit"),
        )

    stats["successful_requests"] += 1
    return UnifiedResponse(
        success=True,
        message=result["message"],
        data=result.get("data"),
        processing_time=time.time() - start_time,
        timestamp=datetime.now().isoformat(),
    )


@router.post("/api/batch-edit-chunks", response_model=UnifiedResponse)
async def batch_edit_chunks(request: ChunkBatchEditRequest, state: RagStateDependency):
    """
    批量编辑文档块

    支持批量修改块内容和启用/禁用状态，自动重新分词和向量化
    """
    start_time = time.time()
    unified_service = state.unified_service
    stats = state.stats
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

    except Exception as error:
        stats["failed_requests"] += 1
        log_rag_failure(logger, stage="chunk_edit", error=error)
        raise HTTPException(
            status_code=500,
            detail=public_error_message("chunk_edit"),
        ) from None

    succeeded = bool(result.get("success"))
    if succeeded:
        stats["successful_requests"] += 1
    else:
        stats["failed_requests"] += 1

    successful_chunks = result.get("successful_chunks", 0)
    failed_chunks = result.get("failed_chunks", 0)
    raw_errors = result.get("errors")
    public_errors = (
        [
            {"error": public_error_message("chunk_edit")}
            for _ in range(min(len(raw_errors), 10))
        ]
        if isinstance(raw_errors, list)
        else []
    )

    return UnifiedResponse(
        success=succeeded,
        message=(
            f"批量编辑完成: 成功 {successful_chunks} 个，失败 {failed_chunks} 个"
            if succeeded
            else public_error_message("chunk_edit")
        ),
        data={
            "total_chunks": result.get("total_chunks", 0),
            "successful_chunks": successful_chunks,
            "failed_chunks": failed_chunks,
            "errors": public_errors,
        },
        processing_time=time.time() - start_time,
        timestamp=datetime.now().isoformat(),
    )
