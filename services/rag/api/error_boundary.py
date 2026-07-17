"""Stable public failures and privacy-safe operational logging for RAG APIs."""

from __future__ import annotations

import hashlib
import logging


_PUBLIC_MESSAGES = {
    "chunking": "文档分块失败",
    "embedding": "文档向量化失败",
    "storing": "文档存储失败",
    "deleting": "文档删除失败",
    "parsing": "文档解析失败",
    "chunk_edit": "文档块编辑失败",
    "recall": "文档检索失败",
    "task_status": "获取任务状态失败",
    "task_list": "列出任务失败",
    "queue_stats": "获取队列统计信息失败",
    "task_cancel": "取消任务失败",
    "task_cleanup": "清理旧任务失败",
}
_LOG_STAGES = frozenset(
    {
        *_PUBLIC_MESSAGES,
        "temp_cleanup",
        "store_cleanup",
        "retriever_cleanup",
        "queue_worker",
        "lease_lost",
    }
)


def public_error_message(stage: str) -> str:
    """Return a stable caller-visible message without exception-derived text."""
    return _PUBLIC_MESSAGES.get(stage, "RAG 服务暂时不可用")


def normalize_failed_task_message(value: object) -> str:
    """Keep only approved failure messages when restoring persistent tasks."""
    normalized = str(value or "").strip()
    if normalized in _PUBLIC_MESSAGES.values():
        return normalized
    return public_error_message("parsing")


def _task_reference(task_id: str | None) -> str | None:
    normalized = str(task_id or "").strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def log_rag_failure(
    target_logger: logging.Logger,
    *,
    stage: str,
    error: BaseException,
    task_id: str | None = None,
) -> None:
    """Log bounded structural metadata without exception messages or tracebacks."""
    safe_stage = stage if stage in _LOG_STAGES else "internal"
    task_reference = _task_reference(task_id)
    if task_reference is None:
        target_logger.error(
            "RAG operation failed: stage=%s error_type=%s",
            safe_stage,
            type(error).__name__,
        )
        return
    target_logger.error(
        "RAG operation failed: stage=%s error_type=%s task_ref=%s",
        safe_stage,
        type(error).__name__,
        task_reference,
    )


def log_rag_worker_result_failure(
    target_logger: logging.Logger,
    *,
    stage: str,
    task_id: str | None = None,
) -> None:
    """Log a failed worker result that did not include an exception object."""
    safe_stage = stage if stage in _LOG_STAGES else "internal"
    task_reference = _task_reference(task_id)
    if task_reference is None:
        target_logger.error(
            "RAG operation failed: stage=%s error_type=WorkerResultFailure",
            safe_stage,
        )
        return
    target_logger.error(
        "RAG operation failed: stage=%s error_type=WorkerResultFailure task_ref=%s",
        safe_stage,
        task_reference,
    )
