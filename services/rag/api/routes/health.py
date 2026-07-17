#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 健康检查路由
"""

from datetime import datetime
from fastapi import APIRouter

from config import settings
from runtime_state import RagStateDependency

router = APIRouter()


@router.get("/")
async def root():
    """根路径 - 服务状态"""
    return {
        "service": "DeepRAG 统一网关服务",
        "version": "1.0.0",
        "status": "running",
        "description": "提供统一的HTTP接口调用DeepRAG的四个核心模块",
        "modules": {
            "chunk_service": "/api/chunk",
            "embedding_service": "/api/embed",
            "store_service": "/api/store",
            "document_parse_service": "/api/parse-document",
            "task_status_service": "/api/task-status/{task_id}",
            "task_list_service": "/api/tasks",
            "queue_stats_service": "/api/queue-stats",
            "cancel_task_service": "/api/task/{task_id}",
            "cleanup_tasks_service": "/api/cleanup-tasks",
            "document_delete_service": "/api/delete-document",
        },
        "supported_formats": list(settings.SUPPORTED_FORMATS),
        "max_file_size": f"{settings.MAX_FILE_SIZE / 1024 / 1024:.0f}MB",
        "chunk_process_workers": settings.CHUNK_PROCESS_WORKERS,
        "api_docs": "/docs",
    }


@router.get("/health")
async def health_check(state: RagStateDependency):
    """健康检查"""
    stats = state.stats
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "uptime": (datetime.now() - stats["start_time"]).total_seconds(),
        "stats": stats,
    }
