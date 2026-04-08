#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 任务管理路由
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

from schemas import TaskStatusResponse, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/task-status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    获取任务状态（包含队列位置信息）
    """
    from app import unified_service

    try:
        task_data = await unified_service.document_parse_service.get_task_status(task_id)

        if not task_data:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

        return TaskStatusResponse(
            success=True,
            task_id=task_id,
            status=task_data["status"],
            progress=task_data["progress"],
            message=task_data["message"],
            data=task_data,
            timestamp=datetime.now().isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取任务状态失败: {str(e)}")


@router.get("/api/tasks", response_model=UnifiedResponse)
async def list_tasks(limit: int = 50, status_filter: Optional[str] = None):
    """
    列出任务
    """
    from app import unified_service

    try:
        tasks = await unified_service.document_parse_service.list_tasks(limit, status_filter)

        return UnifiedResponse(
            success=True,
            message=f"获取到 {len(tasks)} 个任务",
            data={
                "tasks": tasks,
                "total": len(tasks),
                "limit": limit,
                "status_filter": status_filter
            },
            processing_time=0.0,
            timestamp=datetime.now().isoformat()
        )

    except Exception as e:
        logger.error(f"列出任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"列出任务失败: {str(e)}")


@router.get("/api/queue-stats", response_model=UnifiedResponse)
async def get_queue_stats():
    """
    获取队列统计信息
    """
    from app import unified_service

    try:
        queue_stats = await unified_service.document_parse_service.get_queue_stats()

        return UnifiedResponse(
            success=True,
            message="获取队列统计信息成功",
            data=queue_stats,
            processing_time=0.0,
            timestamp=datetime.now().isoformat()
        )

    except Exception as e:
        logger.error(f"获取队列统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取队列统计信息失败: {str(e)}")


@router.delete("/api/task/{task_id}", response_model=UnifiedResponse)
async def cancel_task(task_id: str):
    """
    取消任务
    """
    from app import unified_service

    try:
        cancel_result = await unified_service.document_parse_service.cancel_task(task_id)

        if cancel_result.get("success"):
            state = cancel_result.get("state")
            if state == "cancellation_requested":
                message = f"任务 {task_id} 已收到取消请求，当前步骤结束后将停止后续处理"
            else:
                message = f"任务 {task_id} 已取消"
            return UnifiedResponse(
                success=True,
                message=message,
                data={
                    "task_id": task_id,
                    "cancelled": True,
                    "state": state,
                    "task": cancel_result.get("task"),
                },
                processing_time=0.0,
                timestamp=datetime.now().isoformat()
            )
        elif cancel_result.get("reason") == "not_found":
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在或无法取消")
        else:
            raise HTTPException(status_code=409, detail=f"任务 {task_id} 当前状态无法取消")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"取消任务失败: {str(e)}")


@router.post("/api/cleanup-tasks", response_model=UnifiedResponse)
async def cleanup_old_tasks(max_age_hours: int = 24):
    """
    清理旧任务
    """
    from app import unified_service

    try:
        cleaned_count = await unified_service.document_parse_service.cleanup_old_tasks(max_age_hours)

        return UnifiedResponse(
            success=True,
            message=f"清理了 {cleaned_count} 个旧任务",
            data={"cleaned_count": cleaned_count, "max_age_hours": max_age_hours},
            processing_time=0.0,
            timestamp=datetime.now().isoformat()
        )

    except Exception as e:
        logger.error(f"清理旧任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理旧任务失败: {str(e)}")
