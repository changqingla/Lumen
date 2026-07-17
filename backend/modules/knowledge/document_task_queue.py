"""Durable Redis queue for knowledge document processing.

The database is the source of truth for document state. Redis stores only a
document identifier, retry attempt, and a token-bound visibility lease. This
keeps queue payloads free of tenant-derived paths and lets a restarted worker
resolve every processing parameter from the current database record.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select

from config.redis import get_redis_client
from config.settings import settings
from modules.knowledge.entities.document import Document
from utils.document_process_service import (
    public_document_error,
    sanitize_persisted_document_error,
)

logger = logging.getLogger(__name__)


def _log_queue_failure(
    *,
    stage: str,
    exc: BaseException,
    document_id: str | None = None,
    level: int = logging.ERROR,
) -> None:
    logger.log(
        level,
        "document_queue stage=%s document_id=%s error_type=%s",
        stage,
        document_id or "none",
        type(exc).__name__,
    )


QUEUE_READY_KEY = "knowledge:documents:queue:ready"
QUEUE_LEASED_KEY = "knowledge:documents:queue:leased"
QUEUE_LEASE_TOKENS_KEY = "knowledge:documents:queue:lease_tokens"
QUEUE_ATTEMPTS_KEY = "knowledge:documents:queue:attempts"
QUEUE_CANCELLED_KEY = "knowledge:documents:queue:cancelled"
QUEUE_RECONCILE_LOCK_KEY = "knowledge:documents:queue:reconcile_lock"
QUEUE_RECONCILE_CURSOR_KEY = "knowledge:documents:queue:reconcile_cursor"

DEFAULT_VISIBILITY_TIMEOUT_SECONDS = 120.0
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 5.0
DEFAULT_RECONCILE_INTERVAL_SECONDS = 30.0
DEFAULT_RECONCILE_BATCH_SIZE = 100
DEFAULT_RECONCILE_MAX_DOCUMENTS = 1000
DEFAULT_CANCEL_WAIT_SECONDS = 15.0
WORKER_IDLE_SECONDS = 1.0
REDIS_ERROR_BACKOFF_SECONDS = 2.0

_ENQUEUE_SCRIPT = """
local document_id = ARGV[1]
local attempt = ARGV[2]
local ready_at = tonumber(ARGV[3])
local cancelled_until = redis.call('ZSCORE', KEYS[5], document_id)
if cancelled_until then
    if tonumber(cancelled_until) > ready_at then
        return -1
    end
    redis.call('ZREM', KEYS[5], document_id)
end
if redis.call('ZSCORE', KEYS[1], document_id) or redis.call('ZSCORE', KEYS[2], document_id) then
    return 0
end
redis.call('HSET', KEYS[4], document_id, attempt)
redis.call('ZADD', KEYS[1], ready_at, document_id)
return 1
"""

_ACQUIRE_SCRIPT = """
local now = tonumber(ARGV[1])
local lease_until = tonumber(ARGV[2])
local token = ARGV[3]
for _ = 1, 20 do
    local documents = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, 1)
    if #documents == 0 then
        return {}
    end
    local document_id = documents[1]
    local cancelled_until = redis.call('ZSCORE', KEYS[5], document_id)
    if cancelled_until and tonumber(cancelled_until) > now then
        redis.call('ZREM', KEYS[1], document_id)
        redis.call('HDEL', KEYS[4], document_id)
    else
        if cancelled_until then
            redis.call('ZREM', KEYS[5], document_id)
        end
        if redis.call('ZREM', KEYS[1], document_id) == 1 then
            local attempt = redis.call('HGET', KEYS[4], document_id) or '1'
            redis.call('ZADD', KEYS[2], lease_until, document_id)
            redis.call('HSET', KEYS[3], document_id, token)
            return {document_id, attempt}
        end
    end
end
return {}
"""

_HEARTBEAT_SCRIPT = """
local document_id = ARGV[1]
local token = ARGV[2]
local lease_until = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
if redis.call('HGET', KEYS[3], document_id) ~= token then
    return 0
end
local cancelled_until = redis.call('ZSCORE', KEYS[5], document_id)
if cancelled_until and tonumber(cancelled_until) > now then
    return -1
end
if cancelled_until then
    redis.call('ZREM', KEYS[5], document_id)
end
if not redis.call('ZSCORE', KEYS[2], document_id) then
    return 0
end
redis.call('ZADD', KEYS[2], lease_until, document_id)
return 1
"""

_ACK_SCRIPT = """
local document_id = ARGV[1]
local token = ARGV[2]
if redis.call('HGET', KEYS[3], document_id) ~= token then
    return 0
end
redis.call('ZREM', KEYS[2], document_id)
redis.call('HDEL', KEYS[3], document_id)
redis.call('HDEL', KEYS[4], document_id)
redis.call('ZREM', KEYS[5], document_id)
return 1
"""

_RELEASE_SCRIPT = """
local document_id = ARGV[1]
local token = ARGV[2]
local ready_at = tonumber(ARGV[3])
if redis.call('HGET', KEYS[3], document_id) ~= token then
    return 0
end
redis.call('ZREM', KEYS[2], document_id)
redis.call('HDEL', KEYS[3], document_id)
local cancelled_until = redis.call('ZSCORE', KEYS[5], document_id)
if cancelled_until and tonumber(cancelled_until) > ready_at then
    redis.call('HDEL', KEYS[4], document_id)
    return -1
end
if cancelled_until then
    redis.call('ZREM', KEYS[5], document_id)
end
redis.call('ZADD', KEYS[1], ready_at, document_id)
return 1
"""

_RETRY_SCRIPT = """
local document_id = ARGV[1]
local token = ARGV[2]
local attempt = ARGV[3]
local ready_at = tonumber(ARGV[4])
if redis.call('HGET', KEYS[3], document_id) ~= token then
    return 0
end
redis.call('ZREM', KEYS[2], document_id)
redis.call('HDEL', KEYS[3], document_id)
local cancelled_until = redis.call('ZSCORE', KEYS[5], document_id)
if cancelled_until and tonumber(cancelled_until) > ready_at then
    redis.call('HDEL', KEYS[4], document_id)
    return -1
end
if cancelled_until then
    redis.call('ZREM', KEYS[5], document_id)
end
redis.call('HSET', KEYS[4], document_id, attempt)
redis.call('ZADD', KEYS[1], ready_at, document_id)
return 1
"""

_RECOVER_SCRIPT = """
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', now)
local documents = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, limit)
local recovered = 0
for _, document_id in ipairs(documents) do
    local cancelled_until = redis.call('ZSCORE', KEYS[5], document_id)
    if cancelled_until and tonumber(cancelled_until) > now then
        redis.call('ZADD', KEYS[2], tonumber(cancelled_until), document_id)
    elseif redis.call('ZREM', KEYS[2], document_id) == 1 then
        redis.call('HDEL', KEYS[3], document_id)
        if cancelled_until then
            redis.call('ZREM', KEYS[5], document_id)
        end
        redis.call('ZADD', KEYS[1], now, document_id)
        recovered = recovered + 1
    end
end
return recovered
"""

_CANCEL_SCRIPT = """
local document_id = ARGV[1]
local now = tonumber(ARGV[2])
local cancelled_until = tonumber(ARGV[3])
redis.call('ZADD', KEYS[5], cancelled_until, document_id)
redis.call('ZREM', KEYS[1], document_id)
local lease_until = redis.call('ZSCORE', KEYS[2], document_id)
local lease_token = redis.call('HGET', KEYS[3], document_id)
if not lease_until or not lease_token then
    redis.call('ZREM', KEYS[2], document_id)
    redis.call('HDEL', KEYS[3], document_id)
    redis.call('HDEL', KEYS[4], document_id)
    return 0
end
return 1
"""

_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _queue_keys() -> list[str]:
    return [
        QUEUE_READY_KEY,
        QUEUE_LEASED_KEY,
        QUEUE_LEASE_TOKENS_KEY,
        QUEUE_ATTEMPTS_KEY,
        QUEUE_CANCELLED_KEY,
    ]


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


@dataclass(frozen=True)
class DocumentTaskLease:
    document_id: str
    attempt: int
    token: str


class DocumentTaskCancelled(RuntimeError):
    """Raised when a deletion cancels a running document task."""


class DocumentTaskLeaseLost(RuntimeError):
    """Raised when this worker no longer owns a document lease."""


class RedisDocumentTaskQueue:
    """Token-bound, at-least-once queue implemented with Redis sorted sets."""

    def __init__(self, redis_client: Any, *, visibility_timeout_seconds: float) -> None:
        self.redis = redis_client
        self.visibility_timeout_seconds = max(float(visibility_timeout_seconds), 30.0)

    async def enqueue(
        self, document_id: str, *, attempt: int = 1, ready_at: float | None = None
    ) -> int:
        normalized_id = str(UUID(str(document_id)))
        return int(
            await self.redis.eval(
                _ENQUEUE_SCRIPT,
                5,
                *_queue_keys(),
                normalized_id,
                max(int(attempt), 1),
                time.time() if ready_at is None else float(ready_at),
            )
        )

    async def acquire(self) -> DocumentTaskLease | None:
        now = time.time()
        token = uuid4().hex
        result = await self.redis.eval(
            _ACQUIRE_SCRIPT,
            5,
            *_queue_keys(),
            now,
            now + self.visibility_timeout_seconds,
            token,
        )
        if not result:
            return None
        return DocumentTaskLease(
            document_id=_as_text(result[0]),
            attempt=max(int(_as_text(result[1])), 1),
            token=token,
        )

    async def heartbeat(self, lease: DocumentTaskLease) -> int:
        now = time.time()
        return int(
            await self.redis.eval(
                _HEARTBEAT_SCRIPT,
                5,
                *_queue_keys(),
                lease.document_id,
                lease.token,
                now + self.visibility_timeout_seconds,
                now,
            )
        )

    async def acknowledge(self, lease: DocumentTaskLease) -> bool:
        return bool(
            await self.redis.eval(
                _ACK_SCRIPT,
                5,
                *_queue_keys(),
                lease.document_id,
                lease.token,
            )
        )

    async def release(self, lease: DocumentTaskLease) -> int:
        return int(
            await self.redis.eval(
                _RELEASE_SCRIPT,
                5,
                *_queue_keys(),
                lease.document_id,
                lease.token,
                time.time(),
            )
        )

    async def schedule_retry(
        self, lease: DocumentTaskLease, *, delay_seconds: float
    ) -> int:
        return int(
            await self.redis.eval(
                _RETRY_SCRIPT,
                5,
                *_queue_keys(),
                lease.document_id,
                lease.token,
                lease.attempt + 1,
                time.time() + max(float(delay_seconds), 0.0),
            )
        )

    async def recover_expired(self, *, limit: int = 100) -> int:
        return int(
            await self.redis.eval(
                _RECOVER_SCRIPT,
                5,
                *_queue_keys(),
                time.time(),
                max(int(limit), 1),
            )
        )

    async def cancel(self, document_id: str, *, wait_seconds: float) -> bool:
        normalized_id = str(UUID(str(document_id)))
        now = time.time()
        leased = bool(
            await self.redis.eval(
                _CANCEL_SCRIPT,
                5,
                *_queue_keys(),
                normalized_id,
                now,
                now + max(self.visibility_timeout_seconds * 2, 300.0),
            )
        )
        if not leased:
            return True

        deadline = time.monotonic() + max(float(wait_seconds), 0.0)
        while time.monotonic() < deadline:
            if await self.redis.zscore(QUEUE_LEASED_KEY, normalized_id) is None:
                return True
            await asyncio.sleep(0.1)
        return await self.redis.zscore(QUEUE_LEASED_KEY, normalized_id) is None


class DocumentTaskQueueWorker:
    """Consumes durable document jobs and reconciles DB/Redis state gaps."""

    def __init__(
        self,
        *,
        redis_client: Any,
        session_factory: Any,
        concurrency: int,
        visibility_timeout_seconds: float,
        heartbeat_interval_seconds: float,
        max_retries: int,
        retry_delay_seconds: float,
        reconcile_interval_seconds: float,
        reconcile_batch_size: int,
        reconcile_max_documents: int,
        processor: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self.queue = RedisDocumentTaskQueue(
            redis_client,
            visibility_timeout_seconds=visibility_timeout_seconds,
        )
        self.redis = redis_client
        self.session_factory = session_factory
        self.concurrency = max(int(concurrency), 1)
        self.heartbeat_interval_seconds = min(
            max(float(heartbeat_interval_seconds), 1.0),
            self.queue.visibility_timeout_seconds / 3,
        )
        self.max_retries = max(int(max_retries), 0)
        self.retry_delay_seconds = max(float(retry_delay_seconds), 0.0)
        self.reconcile_interval_seconds = max(float(reconcile_interval_seconds), 5.0)
        self.reconcile_batch_size = max(int(reconcile_batch_size), 1)
        self.reconcile_max_documents = max(
            int(reconcile_max_documents), self.reconcile_batch_size
        )
        self.processor = processor or self._process_document
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(
                self._maintenance_loop(), name="document-queue-maintenance"
            ),
            *[
                asyncio.create_task(
                    self._consume_loop(index), name=f"document-queue-worker-{index}"
                )
                for index in range(self.concurrency)
            ],
        ]
        logger.info(
            "Document queue worker started with concurrency=%s", self.concurrency
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        logger.info("Document queue worker stopped")

    async def _maintenance_loop(self) -> None:
        while self._running:
            try:
                recovered = await self.queue.recover_expired(
                    limit=self.reconcile_batch_size
                )
                if recovered:
                    logger.info(
                        "Recovered %s expired document queue lease(s)", recovered
                    )
                await self.reconcile_queued_documents()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log_queue_failure(stage="maintenance", exc=exc)
            await asyncio.sleep(self.reconcile_interval_seconds)

    async def reconcile_queued_documents(self) -> int:
        """Bounded, lock-protected compensation for the DB-to-Redis enqueue gap."""
        lock_token = uuid4().hex
        lock_seconds = max(int(self.reconcile_interval_seconds * 2), 30)
        acquired = await self.redis.set(
            QUEUE_RECONCILE_LOCK_KEY,
            lock_token,
            nx=True,
            ex=lock_seconds,
        )
        if not acquired:
            return 0

        enqueued = 0
        scanned = 0
        raw_cursor = await self.redis.get(QUEUE_RECONCILE_CURSOR_KEY)
        try:
            cursor = UUID(_as_text(raw_cursor)) if raw_cursor else None
        except (TypeError, ValueError):
            cursor = None
            await self.redis.delete(QUEUE_RECONCILE_CURSOR_KEY)
        try:
            while scanned < self.reconcile_max_documents:
                page_size = min(
                    self.reconcile_batch_size,
                    self.reconcile_max_documents - scanned,
                )
                async with self.session_factory() as db:
                    stmt = select(Document.id).where(
                        Document.status == Document.STATUS_QUEUED
                    )
                    if cursor is not None:
                        stmt = stmt.where(Document.id > cursor)
                    result = await db.execute(
                        stmt.order_by(Document.id.asc()).limit(page_size)
                    )
                    document_ids = list(result.scalars().all())
                if not document_ids:
                    await self.redis.delete(QUEUE_RECONCILE_CURSOR_KEY)
                    if cursor is not None and scanned == 0:
                        cursor = None
                        continue
                    break
                for document_id in document_ids:
                    outcome = await self.queue.enqueue(str(document_id))
                    enqueued += int(outcome > 0)
                scanned += len(document_ids)
                cursor = document_ids[-1]
                await self.redis.set(QUEUE_RECONCILE_CURSOR_KEY, str(cursor))
                if len(document_ids) < page_size:
                    await self.redis.delete(QUEUE_RECONCILE_CURSOR_KEY)
                    break
        finally:
            with contextlib.suppress(Exception):
                await self.redis.eval(
                    _RELEASE_LOCK_SCRIPT,
                    1,
                    QUEUE_RECONCILE_LOCK_KEY,
                    lock_token,
                )
        if enqueued:
            logger.info("Reconciled %s queued document task(s) into Redis", enqueued)
        return enqueued

    async def _consume_loop(self, worker_index: int) -> None:
        while self._running:
            lease: DocumentTaskLease | None = None
            try:
                lease = await self.queue.acquire()
                if lease is None:
                    await asyncio.sleep(WORKER_IDLE_SECONDS)
                    continue
                await self._handle_lease(lease, worker_index)
                lease = None
            except asyncio.CancelledError:
                if lease is not None:
                    with contextlib.suppress(Exception):
                        await self.queue.release(lease)
                raise
            except Exception as exc:
                _log_queue_failure(
                    stage="consume",
                    document_id=lease.document_id if lease is not None else None,
                    exc=exc,
                )
                if lease is not None:
                    with contextlib.suppress(Exception):
                        await self.queue.release(lease)
                await asyncio.sleep(REDIS_ERROR_BACKOFF_SECONDS)

    async def _handle_lease(self, lease: DocumentTaskLease, worker_index: int) -> None:
        try:
            final_status = await self._run_with_heartbeat(lease)
        except DocumentTaskCancelled:
            await self.queue.acknowledge(lease)
            logger.info("Document task %s was cancelled", lease.document_id)
            return
        except DocumentTaskLeaseLost:
            logger.warning(
                "document_queue stage=lease_lost document_id=%s error_type=DocumentTaskLeaseLost",
                lease.document_id,
            )
            return
        except asyncio.CancelledError:
            await self.queue.release(lease)
            raise
        except Exception as exc:
            _log_queue_failure(
                stage="process",
                document_id=lease.document_id,
                exc=exc,
            )
            await self._retry_or_fail(lease, exc)
            return

        if final_status in {Document.STATUS_READY, "missing"}:
            await self.queue.acknowledge(lease)
            return
        if final_status == Document.STATUS_FAILED:
            await self._retry_or_fail(
                lease, RuntimeError("document pipeline reported failure")
            )
            return
        await self._retry_or_fail(
            lease,
            RuntimeError(
                f"document pipeline stopped in unexpected status: {final_status}"
            ),
        )

    async def _run_with_heartbeat(self, lease: DocumentTaskLease) -> str:
        processing_task = asyncio.create_task(self.processor(lease.document_id))
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(lease))
        try:
            done, _ = await asyncio.wait(
                {processing_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                processing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await processing_task
                await heartbeat_task
                raise DocumentTaskLeaseLost(lease.document_id)
            return await processing_task
        finally:
            heartbeat_task.cancel()
            processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            with contextlib.suppress(asyncio.CancelledError):
                await processing_task

    async def _heartbeat_loop(self, lease: DocumentTaskLease) -> None:
        while self._running:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            state = await self.queue.heartbeat(lease)
            if state < 0:
                raise DocumentTaskCancelled(lease.document_id)
            if state == 0:
                raise DocumentTaskLeaseLost(lease.document_id)

    async def _retry_or_fail(
        self, lease: DocumentTaskLease, exc: BaseException
    ) -> None:
        state = await self.queue.heartbeat(lease)
        if state <= 0:
            return
        if lease.attempt <= self.max_retries:
            delay = min(self.retry_delay_seconds * lease.attempt, 300.0)
            scheduled = await self.queue.schedule_retry(lease, delay_seconds=delay)
            if scheduled > 0:
                await self._update_document_state(
                    lease.document_id,
                    status=Document.STATUS_QUEUED,
                    error_message=None,
                )
                logger.warning(
                    "document_queue stage=retry_scheduled document_id=%s attempt=%s delay_seconds=%.1f error_type=%s",
                    lease.document_id,
                    lease.attempt + 1,
                    delay,
                    type(exc).__name__,
                )
            return

        await self._update_document_state(
            lease.document_id,
            status=Document.STATUS_FAILED,
            error_message=public_document_error(exc),
        )
        await self.queue.acknowledge(lease)

    async def _update_document_state(
        self,
        document_id: str,
        *,
        status: str,
        error_message: str | None,
    ) -> None:
        async with self.session_factory() as db:
            result = await db.execute(
                select(Document).where(Document.id == document_id)
            )
            document = result.scalar_one_or_none()
            if document is None or document.status == Document.STATUS_READY:
                return
            if (
                status == Document.STATUS_FAILED
                and document.status == Document.STATUS_FAILED
                and document.error_message
            ):
                error_message = sanitize_persisted_document_error(
                    document.error_message
                )
            document.status = status
            document.error_message = error_message
            await db.commit()

    async def _process_document(self, document_id: str) -> str:
        from modules.knowledge.services.document_service import DocumentService

        async with self.session_factory() as db:
            return await DocumentService(db).process_queued_document(document_id)


def _configured_visibility_timeout() -> float:
    return float(
        _setting(
            "KNOWLEDGE_DOCUMENT_QUEUE_VISIBILITY_TIMEOUT_SECONDS",
            DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
        )
    )


async def enqueue_document_task(document_id: str) -> int:
    redis_client = await get_redis_client()
    queue = RedisDocumentTaskQueue(
        redis_client,
        visibility_timeout_seconds=_configured_visibility_timeout(),
    )
    return await queue.enqueue(document_id)


async def cancel_document_task(document_id: str) -> bool:
    redis_client = await get_redis_client()
    queue = RedisDocumentTaskQueue(
        redis_client,
        visibility_timeout_seconds=_configured_visibility_timeout(),
    )
    configured_heartbeat = max(
        float(
            _setting(
                "KNOWLEDGE_DOCUMENT_QUEUE_HEARTBEAT_INTERVAL_SECONDS",
                DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            )
        ),
        1.0,
    )
    configured_wait = float(
        _setting(
            "KNOWLEDGE_DOCUMENT_QUEUE_CANCEL_WAIT_SECONDS", DEFAULT_CANCEL_WAIT_SECONDS
        )
    )
    return await queue.cancel(
        document_id,
        wait_seconds=max(configured_wait, configured_heartbeat * 2),
    )


_document_task_worker: DocumentTaskQueueWorker | None = None


async def init_document_task_queue(redis_client: Any, session_factory: Any) -> None:
    """Start workers without touching Redis so a transient outage cannot block app startup."""
    global _document_task_worker
    if _document_task_worker is not None:
        return
    _document_task_worker = DocumentTaskQueueWorker(
        redis_client=redis_client,
        session_factory=session_factory,
        concurrency=int(_setting("KNOWLEDGE_DOCUMENT_WORKER_CONCURRENCY", 1)),
        visibility_timeout_seconds=_configured_visibility_timeout(),
        heartbeat_interval_seconds=float(
            _setting(
                "KNOWLEDGE_DOCUMENT_QUEUE_HEARTBEAT_INTERVAL_SECONDS",
                DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            )
        ),
        max_retries=int(
            _setting("KNOWLEDGE_DOCUMENT_QUEUE_MAX_RETRIES", DEFAULT_MAX_RETRIES)
        ),
        retry_delay_seconds=float(
            _setting(
                "KNOWLEDGE_DOCUMENT_QUEUE_RETRY_DELAY_SECONDS",
                DEFAULT_RETRY_DELAY_SECONDS,
            )
        ),
        reconcile_interval_seconds=float(
            _setting(
                "KNOWLEDGE_DOCUMENT_QUEUE_RECONCILE_INTERVAL_SECONDS",
                DEFAULT_RECONCILE_INTERVAL_SECONDS,
            )
        ),
        reconcile_batch_size=int(
            _setting(
                "KNOWLEDGE_DOCUMENT_QUEUE_RECONCILE_BATCH_SIZE",
                DEFAULT_RECONCILE_BATCH_SIZE,
            )
        ),
        reconcile_max_documents=int(
            _setting(
                "KNOWLEDGE_DOCUMENT_QUEUE_RECONCILE_MAX_DOCUMENTS",
                DEFAULT_RECONCILE_MAX_DOCUMENTS,
            )
        ),
    )
    await _document_task_worker.start()


async def shutdown_document_task_queue() -> None:
    global _document_task_worker
    if _document_task_worker is not None:
        await _document_task_worker.stop()
        _document_task_worker = None
