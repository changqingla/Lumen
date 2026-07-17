#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 文档解析服务

提供一体化的文档分块+向量化处理功能，支持异步处理和任务状态查询
基于Redis的任务队列机制，支持高并发和任务排队
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import time
import uuid
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Any, Set
from enum import Enum
from pathlib import Path

from embedding.chunk_embedder import ChunkEmbedder, EmbeddingConfig
from chunk_worker import process_chunk_in_process
from common_utils import DeepRAGCommonUtils
from file_security import normalize_upload_filename

try:
    from .task_metadata import contains_sensitive_task_metadata, sanitize_task_metadata
    from .error_boundary import (
        log_rag_failure,
        log_rag_worker_result_failure,
        normalize_failed_task_message,
        public_error_message,
    )
except ImportError:
    from task_metadata import contains_sensitive_task_metadata, sanitize_task_metadata
    from error_boundary import (
        log_rag_failure,
        log_rag_worker_result_failure,
        normalize_failed_task_message,
        public_error_message,
    )

logger = logging.getLogger(__name__)


class LeaseLostError(RuntimeError):
    """Raised when a worker can no longer mutate its leased task."""


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 等待处理
    QUEUED = "queued"  # 已排队等待
    PROCESSING = "processing"  # 正在处理
    CHUNKING = "chunking"  # 文档分块中
    EMBEDDING = "embedding"  # 向量化中
    STORING = "storing"  # 存储中
    COMPLETED = "completed"  # 完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


class DocumentParseTask:
    """文档解析任务"""

    def __init__(
        self,
        task_id: str,
        filename: str,
        file_size: int,
        chunk_config: Dict[str, Any],
        embedding_config: Dict[str, Any],
        store_config: Dict[str, Any],
    ):
        self.task_id = task_id
        self.filename = filename
        self.file_size = file_size
        self.chunk_config = chunk_config
        self.embedding_config = embedding_config
        self.store_config = store_config

        # 任务状态
        self.status = TaskStatus.PENDING
        self.progress = 0.0  # 0.0 - 1.0
        self.message = "任务已创建，等待处理"
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None

        # 结果数据
        self.total_chunks = 0
        self.processed_chunks = 0
        self.stored_chunks = 0
        self.errors = []
        self.result_data = {}
        self.full_content = ""  # 文档完整内容
        self.source_path: Optional[str] = None
        self.cancel_requested = False

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        failed = self.status == TaskStatus.FAILED
        message = normalize_failed_task_message(self.message) if failed else self.message
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "status": self.status.value,
            "progress": self.progress,
            "message": message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "total_chunks": self.total_chunks,
            "processed_chunks": self.processed_chunks,
            "stored_chunks": self.stored_chunks,
            "errors": [message] if failed else self.errors[-10:],
            "result_data": self.result_data,
            "full_content": self.full_content,  # 文档完整内容（与result_data同级）
            "cancel_requested": self.cancel_requested,
        }

    def to_persisted_dict(self) -> Dict[str, Any]:
        """转换为可持久化的任务元数据"""
        failed = self.status == TaskStatus.FAILED
        message = normalize_failed_task_message(self.message) if failed else self.message
        return sanitize_task_metadata(
            {
                "task_id": self.task_id,
                "filename": self.filename,
                "file_size": self.file_size,
                "chunk_config": self.chunk_config,
                "embedding_config": self.embedding_config,
                "store_config": self.store_config,
                "status": self.status.value,
                "progress": self.progress,
                "message": message,
                "created_at": self.created_at.isoformat(),
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "completed_at": self.completed_at.isoformat()
                if self.completed_at
                else None,
                "total_chunks": self.total_chunks,
                "processed_chunks": self.processed_chunks,
                "stored_chunks": self.stored_chunks,
                "errors": [message] if failed else self.errors[-10:],
                "result_data": self.result_data,
                "source_path": self.source_path,
                "cancel_requested": self.cancel_requested,
            }
        )


class DocumentParseService:
    """文档解析服务类 - 基于Redis任务队列"""

    def __init__(self, max_concurrent_tasks: int = 10):
        self.utils = DeepRAGCommonUtils()
        self.tasks: Dict[str, DocumentParseTask] = {}  # 任务存储
        self.max_concurrent_tasks = max_concurrent_tasks
        self._task_lock = asyncio.Lock()

        # Redis任务队列
        try:
            from .redis_task_queue import RedisTaskQueue, QueuePriority
        except ImportError:
            # 当作为脚本直接运行时，使用绝对导入
            from redis_task_queue import RedisTaskQueue, QueuePriority

        # 统一配置
        try:
            from .config import settings as _settings
        except ImportError:
            from config import settings as _settings
        self._settings = _settings
        self._blocking_executor: Executor | None = ThreadPoolExecutor(
            max_workers=max(1, int(_settings.CHUNK_PROCESS_WORKERS)),
            thread_name_prefix="lumen-rag-document",
        )

        self.task_queue = RedisTaskQueue(
            redis_host=_settings.REDIS_HOST,
            redis_port=_settings.REDIS_PORT,
            redis_username=_settings.REDIS_USERNAME,
            redis_password=_settings.REDIS_PASSWORD,
            redis_db=_settings.REDIS_DB,
            max_concurrent_tasks=max_concurrent_tasks,
            visibility_timeout_seconds=_settings.TASK_VISIBILITY_TIMEOUT_SECONDS,
        )
        self.QueuePriority = QueuePriority

        # 后台任务处理器
        self._worker_tasks: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()
        self._workers_started = False
        self._active_leases: Dict[str, str] = {}
        self._recovery_lock = asyncio.Lock()
        self._next_recovery_at = 0.0
        self._heartbeat_interval = _settings.TASK_HEARTBEAT_INTERVAL_SECONDS
        self._recovery_interval = _settings.TASK_STALE_RECOVERY_INTERVAL_SECONDS
        self._payload_dir = Path(_settings.TEMP_DIR) / "task_payloads"
        self._payload_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """初始化服务"""
        async with self._task_lock:
            # 初始化Redis任务队列
            if not await self.task_queue.initialize():
                raise RuntimeError("Redis任务队列初始化失败")

            await self._recover_stale_tasks_if_due(force=True)

            # 启动后台工作进程
            if not self._workers_started:
                await self.start_workers()
                self._workers_started = True

    async def _run_blocking(self, function, *args):
        """Run parser/provider sync work on the service-owned executor."""
        executor = getattr(self, "_blocking_executor", None)
        if executor is None:
            raise RuntimeError("Document parse blocking executor is unavailable")
        blocking_future = asyncio.get_running_loop().run_in_executor(
            executor,
            function,
            *args,
        )
        try:
            return await asyncio.shield(blocking_future)
        except asyncio.CancelledError:
            # ThreadPoolExecutor cannot stop a running function. Wait for it to
            # leave the lease-specific workspace before cancellation cleanup.
            await blocking_future
            raise

    async def start_workers(self, num_workers: int = None):
        """启动后台工作进程"""
        if num_workers is None:
            num_workers = min(self.max_concurrent_tasks, 5)  # 最多5个worker

        logger.info(f"启动 {num_workers} 个后台工作进程")

        for i in range(num_workers):
            worker_task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._worker_tasks.add(worker_task)
            worker_task.add_done_callback(self._worker_tasks.discard)

    def _payload_path_for(self, task_id: str, filename: str) -> Path:
        safe_name = normalize_upload_filename(filename)
        task_dir = self._payload_dir / self._opaque_path_component(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        payload_path = task_dir / safe_name
        payload_path.resolve().relative_to(task_dir.resolve())
        return payload_path

    @staticmethod
    def _opaque_path_component(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    def _validated_payload_path(self, value: object) -> Path | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        candidate = Path(normalized)
        try:
            managed_root = self._payload_dir.resolve()
            resolved = candidate.resolve()
            resolved.relative_to(managed_root)
        except (OSError, RuntimeError, ValueError):
            return None
        if candidate.is_symlink() or resolved.parent == managed_root:
            return None
        return resolved

    def _cleanup_task_payload(self, task: DocumentParseTask) -> None:
        if not task.source_path:
            return

        payload_path = self._validated_payload_path(task.source_path)
        if payload_path is not None:
            shutil.rmtree(payload_path.parent, ignore_errors=True)
        task.source_path = None

    @staticmethod
    def _write_task_payload(payload_path: Path, file_content: bytes) -> None:
        """Publish a task payload atomically so workers never observe a partial file."""
        temporary_path = payload_path.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary_path.open("xb") as payload_file:
                payload_file.write(file_content)
                payload_file.flush()
                os.fsync(payload_file.fileno())
            os.replace(temporary_path, payload_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    async def _persist_task(self, task: DocumentParseTask) -> None:
        self.tasks[task.task_id] = task
        # Include process-level credentials only as redaction context. The
        # sanitizer removes this context and redacts provider errors that may
        # have echoed a credential before anything reaches Redis.
        persisted = sanitize_task_metadata(
            {
                "task": task.to_persisted_dict(),
                "credentials": {
                    "embedding_api_key": getattr(
                        self._settings, "EMBEDDING_API_KEY", ""
                    ),
                    "cv_api_key": getattr(self._settings, "CV_API_KEY", ""),
                    "es_password": getattr(self._settings, "ES_PASSWORD", ""),
                },
            }
        )["task"]
        lease_token = getattr(self, "_active_leases", {}).get(task.task_id)
        if lease_token is not None:
            updated = await self.task_queue.update_task_data(
                task.task_id,
                persisted,
                lease_token=lease_token,
            )
            if updated is None:
                raise LeaseLostError(f"Task {task.task_id} no longer owns its lease")
            task.cancel_requested = bool(updated.get("cancel_requested", False))
            return

        updated = await self.task_queue.update_task_data(task.task_id, persisted)
        if updated is None:
            await self.task_queue.set_task_data(task.task_id, persisted)

    def _task_from_data(self, task_data: Dict[str, Any]) -> DocumentParseTask:
        task = DocumentParseTask(
            task_id=task_data["task_id"],
            filename=normalize_upload_filename(task_data["filename"]),
            file_size=task_data["file_size"],
            chunk_config=task_data.get("chunk_config", {}),
            embedding_config=task_data.get("embedding_config", {}),
            store_config=task_data.get("store_config", {}),
        )
        task.status = TaskStatus(task_data.get("status", TaskStatus.PENDING.value))
        task.progress = task_data.get("progress", 0.0)
        task.message = task_data.get("message", task.message)
        created_at = task_data.get("created_at")
        started_at = task_data.get("started_at")
        completed_at = task_data.get("completed_at")
        if created_at:
            task.created_at = datetime.fromisoformat(created_at)
        if started_at:
            task.started_at = datetime.fromisoformat(started_at)
        if completed_at:
            task.completed_at = datetime.fromisoformat(completed_at)
        task.total_chunks = task_data.get("total_chunks", 0)
        task.processed_chunks = task_data.get("processed_chunks", 0)
        task.stored_chunks = task_data.get("stored_chunks", 0)
        task.errors = task_data.get("errors", [])
        if task.status == TaskStatus.FAILED:
            task.message = normalize_failed_task_message(task.message)
            task.errors = [task.message]
        task.result_data = task_data.get("result_data", {})
        source_path = self._validated_payload_path(task_data.get("source_path"))
        task.source_path = str(source_path) if source_path is not None else None
        task.cancel_requested = bool(task_data.get("cancel_requested", False))
        return task

    async def _restore_task(self, task_id: str) -> Optional[DocumentParseTask]:
        task_data = await self.task_queue.get_task_data(task_id)
        if not task_data:
            return None

        task = self._task_from_data(task_data)
        self.tasks[task_id] = task
        if contains_sensitive_task_metadata(task_data) or (
            task.status == TaskStatus.FAILED
            and (
                task_data.get("message") != task.message
                or task_data.get("errors") != task.errors
            )
        ):
            await self._persist_task(task)
        return task

    async def _get_or_restore_task(self, task_id: str) -> Optional[DocumentParseTask]:
        task_data = await self.task_queue.get_task_data(task_id)
        if task_data:
            task = self._task_from_data(task_data)
            self.tasks[task_id] = task
            if contains_sensitive_task_metadata(task_data) or (
                task.status == TaskStatus.FAILED
                and (
                    task_data.get("message") != task.message
                    or task_data.get("errors") != task.errors
                )
            ):
                await self._persist_task(task)
            return task

        return self.tasks.get(task_id)

    async def _finalize_cancelled_task(
        self, task: DocumentParseTask, message: str
    ) -> Dict[str, Any]:
        task.status = TaskStatus.CANCELLED
        task.progress = min(task.progress, 0.95)
        task.message = message
        task.completed_at = datetime.now()
        task.cancel_requested = True
        await self._persist_task(task)
        return {
            "success": False,
            "cancelled": True,
            "message": message,
            "task_id": task.task_id,
        }

    async def _task_payload_available(self, task_id: str) -> bool:
        task_data = await self.task_queue.get_task_data(task_id)
        source_path = task_data.get("source_path") if task_data else None
        if not source_path:
            local_task = self.tasks.get(task_id)
            source_path = local_task.source_path if local_task else None
        payload_path = self._validated_payload_path(source_path)
        return bool(payload_path and payload_path.is_file())

    async def _recover_stale_tasks(self) -> int:
        recovered = 0
        for lease in await self.task_queue.get_stale_task_leases():
            payload_available = await self._task_payload_available(lease.task_id)
            disposition = await self.task_queue.recover_stale_task(
                lease.task_id,
                lease.token,
                payload_available=payload_available,
            )
            if disposition in {"requeued", "failed"}:
                recovered += 1
                self.tasks.pop(lease.task_id, None)
                logger.warning(
                    "Recovered stale document task %s as %s",
                    lease.task_id,
                    disposition,
                )
        return recovered

    async def _recover_stale_tasks_if_due(self, *, force: bool = False) -> int:
        now = time.monotonic()
        if not force and now < self._next_recovery_at:
            return 0

        async with self._recovery_lock:
            now = time.monotonic()
            if not force and now < self._next_recovery_at:
                return 0
            self._next_recovery_at = now + self._recovery_interval
            return await self._recover_stale_tasks()

    async def _process_with_heartbeat(
        self, task_id: str, lease_token: str
    ) -> Dict[str, Any]:
        processing_task = asyncio.create_task(
            self.process_document_async(task_id, lease_token=lease_token)
        )
        try:
            while True:
                done, _ = await asyncio.wait(
                    {processing_task},
                    timeout=self._heartbeat_interval,
                )
                if done:
                    return await processing_task
                if not await self.task_queue.heartbeat_task(task_id, lease_token):
                    raise LeaseLostError(f"Task {task_id} lease heartbeat was rejected")
                await self._recover_stale_tasks_if_due()
        finally:
            if not processing_task.done():
                processing_task.cancel()
                await asyncio.gather(processing_task, return_exceptions=True)

    async def _requeue_owned_task(self, task_id: str, lease_token: str) -> str:
        payload_available = await self._task_payload_available(task_id)
        disposition = await self.task_queue.requeue_task(
            task_id,
            lease_token,
            payload_available=payload_available,
        )
        if disposition in {"requeued", "failed"}:
            self.tasks.pop(task_id, None)
        return disposition

    async def _process_lease(
        self, worker_name: str, task_id: str, lease_token: str
    ) -> None:
        self._active_leases[task_id] = lease_token
        lease_released = False
        try:
            logger.info("工作进程 %s 开始处理任务 %s", worker_name, task_id)
            result = await self._process_with_heartbeat(task_id, lease_token)

            terminal = False
            if result.get("cancelled", False):
                lease_released = await self.task_queue.mark_task_cancelled(
                    task_id, lease_token
                )
                terminal = lease_released
            elif result.get("success", False):
                lease_released = await self.task_queue.complete_task(
                    task_id, lease_token
                )
                terminal = lease_released
            else:
                disposition = await self.task_queue.fail_task_with_result(
                    task_id,
                    lease_token,
                    result.get("message", "处理失败"),
                )
                lease_released = disposition in {"requeued", "failed"}
                terminal = disposition == "failed"

            if lease_released and terminal:
                task = self.tasks.get(task_id)
                if task:
                    self._cleanup_task_payload(task)
            if not lease_released:
                logger.warning("任务 %s 的终态提交因 lease 失效被拒绝", task_id)
            logger.info("工作进程 %s 完成任务 %s", worker_name, task_id)
        except LeaseLostError as error:
            log_rag_failure(
                logger,
                stage="lease_lost",
                error=error,
                task_id=task_id,
            )
        finally:
            if self._shutdown_event.is_set() and not lease_released:
                disposition = await asyncio.shield(
                    self._requeue_owned_task(task_id, lease_token)
                )
                lease_released = disposition in {"requeued", "failed"}
            if self._active_leases.get(task_id) == lease_token and (
                lease_released or not self._shutdown_event.is_set()
            ):
                self._active_leases.pop(task_id, None)

    async def _worker_loop(self, worker_name: str):
        """工作进程循环"""
        logger.info(f"工作进程 {worker_name} 已启动")

        while not self._shutdown_event.is_set():
            try:
                await self._recover_stale_tasks_if_due()
                lease = await self.task_queue.dequeue_task()

                if lease:
                    await self._process_lease(worker_name, lease.task_id, lease.token)
                else:
                    # 没有任务，等待一段时间
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info(f"工作进程 {worker_name} 被取消")
                break
            except Exception as error:
                log_rag_failure(logger, stage="queue_worker", error=error)
                await asyncio.sleep(5)  # 出错后等待5秒再继续

        logger.info(f"工作进程 {worker_name} 已停止")

    async def create_task(
        self,
        filename: str,
        file_content: bytes,
        chunk_config: Dict[str, Any],
        embedding_config: Dict[str, Any],
        store_config: Dict[str, Any],
        priority: str = "normal",
        idempotency_key: str | None = None,
    ) -> str:
        """
        创建文档解析任务并加入队列

        Args:
            filename: 文件名
            file_content: 文件内容
            chunk_config: 分块配置
            embedding_config: 向量化配置
            store_config: 存储配置
            priority: 任务优先级 (low, normal, high, urgent)

        Returns:
            任务ID
        """
        # 确保服务已初始化
        if not self._workers_started:
            await self.initialize()

        normalized_idempotency_key = str(idempotency_key or "").strip().lower()
        if (
            normalized_idempotency_key
            and re.fullmatch(r"[0-9a-f]{64}", normalized_idempotency_key) is None
        ):
            raise ValueError("idempotency_key must be a lowercase SHA-256 digest")

        task_id = str(uuid.uuid4())
        safe_filename = normalize_upload_filename(filename)

        # 创建任务对象
        task = DocumentParseTask(
            task_id=task_id,
            filename=safe_filename,
            file_size=len(file_content),
            chunk_config=sanitize_task_metadata(chunk_config),
            embedding_config=sanitize_task_metadata(embedding_config),
            store_config=sanitize_task_metadata(store_config),
        )

        # 设置任务状态为排队
        task.status = TaskStatus.QUEUED
        task.message = "任务已创建，正在排队等待处理"
        task.source_path = str(self._payload_path_for(task_id, safe_filename))
        self._write_task_payload(Path(task.source_path), file_content)

        # 解析优先级
        priority_map = {
            "low": self.QueuePriority.LOW,
            "normal": self.QueuePriority.NORMAL,
            "high": self.QueuePriority.HIGH,
            "urgent": self.QueuePriority.URGENT,
        }
        task_priority = priority_map.get(priority.lower(), self.QueuePriority.NORMAL)

        try:
            if normalized_idempotency_key:
                resolved_task_id, created = await self.task_queue.claim_idempotent_task(
                    task_id,
                    normalized_idempotency_key,
                    task_priority,
                    task_data=task.to_persisted_dict(),
                )
                if not created:
                    self._cleanup_task_payload(task)
                    await self._restore_task(resolved_task_id)
                    logger.info(
                        "Reused document parse task %s for idempotency key",
                        resolved_task_id,
                    )
                    return resolved_task_id
                success = True
            else:
                success = await self.task_queue.enqueue_task(
                    task_id,
                    task_priority,
                    task_data=task.to_persisted_dict(),
                )
        except Exception:
            self._cleanup_task_payload(task)
            raise

        if success:
            self.tasks[task_id] = task
            logger.info(
                "创建文档解析任务: %s, 优先级: %s",
                task_id,
                priority,
            )
        else:
            self._cleanup_task_payload(task)
            logger.error(f"任务 {task_id} 加入队列失败")
            raise RuntimeError("Document parse task could not be enqueued")

        return task_id

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务状态（包含队列位置信息）

        Args:
            task_id: 任务ID

        Returns:
            任务状态信息
        """
        task = await self._get_or_restore_task(task_id)
        if not task:
            return None

        task_dict = task.to_dict()

        # 如果任务在队列中，获取队列位置
        if task.status == TaskStatus.QUEUED:
            position = await self.task_queue.get_task_position(task_id)
            if position:
                task_dict["queue_position"] = position
                task_dict["message"] = f"任务排队中，当前位置: {position}"

        return task_dict

    async def list_tasks(
        self, limit: int = 50, status_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        列出任务

        Args:
            limit: 返回任务数量限制
            status_filter: 状态过滤

        Returns:
            任务列表
        """
        persisted_tasks = await self.task_queue.list_task_data()
        for task_id in persisted_tasks:
            if task_id not in self.tasks:
                await self._restore_task(task_id)

        tasks = list(self.tasks.values())

        # 按状态过滤
        if status_filter:
            tasks = [t for t in tasks if t.status.value == status_filter]

        # 按创建时间排序（最新的在前）
        tasks.sort(key=lambda x: x.created_at, reverse=True)

        # 限制数量
        tasks = tasks[:limit]

        return [task.to_dict() for task in tasks]

    async def process_document_async(
        self,
        task_id: str,
        *,
        lease_token: str | None = None,
    ) -> Dict[str, Any]:
        """
        异步处理文档解析任务（由工作进程调用）

        Args:
            task_id: 任务ID

        Returns:
            处理结果
        """
        task = await self._get_or_restore_task(task_id)
        if not task:
            return {
                "success": False,
                "message": f"任务 {task_id} 不存在",
                "task_id": task_id,
            }

        try:
            if task.cancel_requested:
                return await self._finalize_cancelled_task(
                    task, "任务在开始处理前已取消"
                )

            # 更新任务状态
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "开始处理文档"
            task.progress = 0.0
            await self._persist_task(task)

            # 步骤1: 文档分块
            task.status = TaskStatus.CHUNKING
            task.message = "开始处理文档分块"
            task.progress = 0.1
            await self._persist_task(task)

            logger.info(f"开始处理文档解析任务: {task_id}")

            # 步骤1: 文档分块
            chunk_result = await self._process_chunking(task, lease_token=lease_token)
            if task.cancel_requested:
                return await self._finalize_cancelled_task(
                    task, "已收到取消请求，文档分块完成后停止后续处理"
                )
            if not chunk_result["success"]:
                task.status = TaskStatus.FAILED
                task.message = public_error_message("chunking")
                task.errors = [task.message]
                task.completed_at = datetime.now()
                await self._persist_task(task)
                return {
                    "success": False,
                    "message": task.message,
                    "task_id": task_id,
                }

            chunks = chunk_result["chunks"]
            full_content = chunk_result.get("full_content", "")  # 获取完整内容
            task.total_chunks = len(chunks)
            task.progress = 0.4
            await self._persist_task(task)

            # 步骤2: 向量化
            task.status = TaskStatus.EMBEDDING
            task.message = f"开始向量化 {len(chunks)} 个分块"
            await self._persist_task(task)

            embedding_result = await self._process_embedding(task, chunks)
            if task.cancel_requested:
                return await self._finalize_cancelled_task(
                    task, "已收到取消请求，向量化完成后停止后续处理"
                )
            if not embedding_result["success"]:
                task.status = TaskStatus.FAILED
                task.message = public_error_message("embedding")
                task.errors = [task.message]
                task.completed_at = datetime.now()
                await self._persist_task(task)
                return {
                    "success": False,
                    "message": task.message,
                    "task_id": task_id,
                }

            task.processed_chunks = len(chunks)
            task.progress = 0.7
            await self._persist_task(task)

            if task.cancel_requested:
                return await self._finalize_cancelled_task(
                    task, "已收到取消请求，已在存储前停止任务"
                )

            # 步骤3: 存储
            task.status = TaskStatus.STORING
            task.message = f"开始存储 {len(chunks)} 个向量化分块"
            await self._persist_task(task)

            store_result = await self._process_storing(task, chunks)
            if not store_result["success"]:
                task.status = TaskStatus.FAILED
                task.message = public_error_message("storing")
                task.errors = [task.message]
                task.completed_at = datetime.now()
                await self._persist_task(task)
                return {
                    "success": False,
                    "message": task.message,
                    "task_id": task_id,
                }

            # 任务完成
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.stored_chunks = store_result.get("stored_count", 0)
            task.message = f"处理完成: 成功存储 {task.stored_chunks} 个分块"
            task.completed_at = datetime.now()
            task.full_content = full_content  # 保存完整内容到外层

            # 保存结果数据（不再包含full_content）
            task.result_data = {
                "total_chunks": task.total_chunks,
                "stored_chunks": task.stored_chunks,
                "vector_dimension": embedding_result.get("vector_dimension", 0),
                "total_tokens": embedding_result.get("token_count", 0),
                "processing_time": (
                    task.completed_at - task.started_at
                ).total_seconds(),
                "index_name": task.store_config.get("index_name"),
            }
            await self._persist_task(task)

            logger.info(f"文档解析任务完成: {task_id}")

            return {
                "success": True,
                "message": task.message,
                "task_id": task_id,
                "data": task.result_data,
            }

        except LeaseLostError:
            raise
        except Exception as error:
            log_rag_failure(
                logger,
                stage="parsing",
                error=error,
                task_id=task_id,
            )

            task = self.tasks.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.message = public_error_message("parsing")
                task.completed_at = datetime.now()
                task.errors = [task.message]
                await self._persist_task(task)

            return {
                "success": False,
                "message": public_error_message("parsing"),
                "task_id": task_id,
            }

    async def _process_chunking(
        self,
        task: DocumentParseTask,
        *,
        lease_token: str | None = None,
    ) -> Dict[str, Any]:
        """处理文档分块"""
        try:
            # 保存临时文件
            temp_dir = Path(
                getattr(self._settings, "TEMP_DIR", "/tmp/deeprag_parse")
            ) / "processing"
            task_root = temp_dir / self._opaque_path_component(task.task_id)
            lease_reference = self._opaque_path_component(
                lease_token or uuid.uuid4().hex
            )
            task_temp_dir = task_root / lease_reference
            task_temp_dir.mkdir(parents=True, exist_ok=False)
            safe_filename = normalize_upload_filename(task.filename)
            temp_file_path = task_temp_dir / safe_filename
            temp_file_path.resolve().relative_to(task_temp_dir.resolve())
            source_path = self._validated_payload_path(task.source_path)
            if source_path is None or not source_path.is_file():
                raise FileNotFoundError("Task source payload is unavailable")

            shutil.copy2(source_path, temp_file_path)

            try:
                # 准备视觉解析参数（如果需要）
                cv_model_config = self._resolve_cv_config(task)
                vision_kwargs = None
                if cv_model_config:
                    vision_kwargs = {
                        "vision_dpi": task.chunk_config.get("vision_dpi", 50),
                        "vision_batch_size": task.chunk_config.get(
                            "vision_batch_size", 10
                        ),
                        "vision_keep_images": task.chunk_config.get(
                            "vision_keep_images", False
                        ),
                        "vision_use_custom_prompt": task.chunk_config.get(
                            "vision_use_custom_prompt", False
                        ),
                        "vision_custom_prompt": task.chunk_config.get(
                            "vision_custom_prompt", None
                        ),
                    }

                # 执行分块处理
                result = await self._run_blocking(
                    process_chunk_in_process,
                    str(temp_file_path),
                    task.chunk_config.get("parser_type", "auto"),
                    task.chunk_config.get("chunk_token_num", 256),
                    task.chunk_config.get("delimiter", "\n。；！？"),
                    task.chunk_config.get("language", "Chinese"),
                    task.chunk_config.get("layout_recognize", "DeepDOC"),
                    task.chunk_config.get("zoomin", 3),
                    task.chunk_config.get("from_page", 0),
                    task.chunk_config.get("to_page", 100000),
                    task.chunk_config.get("document_id"),
                    cv_model_config,
                    vision_kwargs,
                    task.chunk_config.get("ir_table_config"),
                )

                if result["success"]:
                    return {
                        "success": True,
                        "chunks": result["chunks"],
                        "full_content": result.get(
                            "full_content", ""
                        ),  # 新增：完整内容
                        "message": f"成功分块，生成 {len(result['chunks'])} 个分块",
                    }
                else:
                    log_rag_worker_result_failure(
                        logger,
                        stage="chunking",
                        task_id=task.task_id,
                    )
                    return {
                        "success": False,
                        "message": public_error_message("chunking"),
                    }

            finally:
                # 清理临时目录和文件
                if task_temp_dir.exists():
                    shutil.rmtree(task_temp_dir)
                try:
                    task_root.rmdir()
                except OSError:
                    pass

        except Exception as error:
            log_rag_failure(
                logger,
                stage="chunking",
                error=error,
                task_id=task.task_id,
            )
            return {"success": False, "message": public_error_message("chunking")}

    async def _process_embedding(
        self, task: DocumentParseTask, chunks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """处理向量化"""
        try:
            embedding_config = self._resolve_embedding_config(task)
            # 创建向量化配置
            config = EmbeddingConfig(
                model_factory=embedding_config["model_factory"],
                model_name=embedding_config["model_name"],
                api_key=embedding_config.get("api_key", ""),
                base_url=embedding_config.get("base_url"),
                batch_size=embedding_config.get("batch_size", 16),
                filename_embd_weight=embedding_config.get("filename_embd_weight", 0.1),
            )

            # 执行向量化
            embedder = ChunkEmbedder(config)

            token_count, vector_size = await self._run_blocking(
                embedder.embed_chunks_sync,
                chunks,
            )

            return {
                "success": True,
                "token_count": token_count,
                "vector_dimension": vector_size,
                "message": f"成功向量化 {len(chunks)} 个分块",
            }

        except Exception as error:
            log_rag_failure(
                logger,
                stage="embedding",
                error=error,
                task_id=task.task_id,
            )
            return {"success": False, "message": public_error_message("embedding")}

    async def _process_storing(
        self, task: DocumentParseTask, chunks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """处理存储"""
        try:
            store_config = self._resolve_store_config(task)
            # 创建文档存储器
            store = self.utils.create_document_store(
                es_host=store_config["es_host"],
                index_name=store_config["index_name"],
                username=store_config.get("username"),
                password=store_config.get("password"),
                timeout=store_config.get("timeout", 60),
            )

            # 执行异步存储（不再使用线程池）
            try:
                success_count, errors = await store.store_chunks(
                    chunks,
                    store_config.get("batch_size", 100),
                )
            finally:
                # 清理ES连接
                try:
                    await store.close()
                except Exception as error:
                    log_rag_failure(
                        logger,
                        stage="store_cleanup",
                        error=error,
                        task_id=task.task_id,
                    )

            if errors:
                logger.error(
                    "RAG operation failed: stage=storing error_count=%s",
                    len(errors),
                )
                return {
                    "success": False,
                    "stored_count": success_count,
                    "errors": [],
                    "message": public_error_message("storing"),
                }
            return {
                "success": True,
                "stored_count": success_count,
                "errors": [],
                "message": f"成功存储 {success_count} 个分块",
            }

        except Exception as error:
            log_rag_failure(
                logger,
                stage="storing",
                error=error,
                task_id=task.task_id,
            )
            return {"success": False, "message": public_error_message("storing")}

    def _current_config_value(self, setting_name: str, fallback: Any = None) -> Any:
        """Resolve a worker setting, falling back only for legacy queued tasks."""
        configured = getattr(self._settings, setting_name, None)
        if configured is None:
            return fallback
        if isinstance(configured, str) and not configured.strip():
            return fallback
        return configured

    def _resolve_embedding_config(self, task: DocumentParseTask) -> Dict[str, Any]:
        resolved = dict(task.embedding_config)
        resolved["api_key"] = self._current_config_value(
            "EMBEDDING_API_KEY",
            task.embedding_config.get("api_key", ""),
        )
        resolved["base_url"] = self._current_config_value(
            "EMBEDDING_BASE_URL",
            task.embedding_config.get("base_url"),
        )
        return resolved

    def _resolve_cv_config(self, task: DocumentParseTask) -> Optional[Dict[str, Any]]:
        task_config = task.chunk_config.get("cv_model_config")
        if not task_config:
            return None

        resolved = dict(task_config)
        resolved["api_key"] = self._current_config_value(
            "CV_API_KEY",
            task_config.get("api_key", ""),
        )
        resolved["base_url"] = self._current_config_value(
            "CV_BASE_URL",
            task_config.get("base_url"),
        )
        return resolved

    def _resolve_store_config(self, task: DocumentParseTask) -> Dict[str, Any]:
        resolved = dict(task.store_config)
        resolved["es_host"] = self._current_config_value(
            "ES_HOST",
            task.store_config.get("es_host"),
        )
        resolved["username"] = self._current_config_value(
            "ES_USERNAME",
            task.store_config.get("username"),
        )
        resolved["password"] = self._current_config_value(
            "ES_PASSWORD",
            task.store_config.get("password"),
        )
        return resolved

    async def get_queue_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        return await self.task_queue.get_queue_stats()

    async def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """取消任务"""
        task = await self._get_or_restore_task(task_id)
        if not task:
            return {"success": False, "reason": "not_found"}

        active_statuses = {
            TaskStatus.PROCESSING,
            TaskStatus.CHUNKING,
            TaskStatus.EMBEDDING,
            TaskStatus.STORING,
        }

        if task.status in active_statuses or await self.task_queue.is_task_processing(
            task_id
        ):
            task.cancel_requested = True
            task.message = "已收到取消请求，当前步骤结束后将停止后续处理"
            await self._persist_task(task)
            return {
                "success": True,
                "state": "cancellation_requested",
                "task": task.to_dict(),
            }

        removed = await self.task_queue.remove_queued_task(task_id)
        if removed or task.status in {TaskStatus.PENDING, TaskStatus.QUEUED}:
            task.status = TaskStatus.CANCELLED
            task.cancel_requested = True
            task.message = "任务已取消"
            task.completed_at = datetime.now()
            self._cleanup_task_payload(task)
            await self._persist_task(task)
            return {"success": True, "state": "cancelled", "task": task.to_dict()}

        return {"success": False, "reason": "not_cancellable"}

    async def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """清理旧任务"""
        # 清理Redis中的过期任务
        redis_cleaned = await self.task_queue.cleanup_expired_tasks(max_age_hours)

        persisted_tasks = await self.task_queue.list_task_data()
        for task_id in persisted_tasks:
            if task_id not in self.tasks:
                await self._restore_task(task_id)

        # 清理本地任务存储
        current_time = datetime.now()
        to_remove = []

        for task_id, task in self.tasks.items():
            age_hours = (current_time - task.created_at).total_seconds() / 3600
            if age_hours > max_age_hours:
                to_remove.append(task_id)

        for task_id in to_remove:
            task = self.tasks.pop(task_id)
            self._cleanup_task_payload(task)
            await self.task_queue.delete_task_data(task_id)
            logger.info(f"清理旧任务: {task_id}")

        total_cleaned = redis_cleaned + len(to_remove)
        logger.info(f"总共清理了 {total_cleaned} 个旧任务")

        return total_cleaned

    async def shutdown(self):
        """关闭服务"""
        logger.info("正在关闭文档解析服务...")

        # 设置关闭事件
        self._shutdown_event.set()

        # Cancel workers so their finally blocks can atomically return any
        # currently owned leases without consuming retry attempts.
        workers = list(self._worker_tasks)
        if workers:
            logger.info(f"正在停止 {len(workers)} 个工作进程...")
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        # Best effort fallback for a worker that exited before running cleanup.
        for task_id, lease_token in list(self._active_leases.items()):
            await asyncio.shield(self._requeue_owned_task(task_id, lease_token))
            if self._active_leases.get(task_id) == lease_token:
                self._active_leases.pop(task_id, None)

        blocking_executor = getattr(self, "_blocking_executor", None)
        if blocking_executor is not None:
            blocking_executor.shutdown(wait=True, cancel_futures=True)
            self._blocking_executor = None

        # 关闭Redis连接
        await self.task_queue.close()

        logger.info("文档解析服务已关闭")
