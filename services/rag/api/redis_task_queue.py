#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Redis任务队列管理器

基于Redis实现的分布式任务队列，支持：
- 任务排队和优先级
- 并发控制
- 任务状态管理
- 失败重试
- 任务监控
"""

import asyncio
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, fields
from enum import Enum
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import quote

import redis.asyncio as redis

try:
    from .error_boundary import log_rag_failure
    from .task_metadata import sanitize_task_metadata
except ImportError:
    from error_boundary import log_rag_failure
    from task_metadata import sanitize_task_metadata

logger = logging.getLogger(__name__)


def _log_queue_failure(error: BaseException, task_id: str | None = None) -> None:
    log_rag_failure(
        logger,
        stage="queue_worker",
        error=error,
        task_id=task_id,
    )


# Keep priority as the dominant component while reversing an atomic Redis
# sequence so ZPOPMAX selects the oldest task within the highest priority.
QUEUE_PRIORITY_STRIDE = 1_000_000_000_000_000.0
LEGACY_QUEUE_SCORE_CUTOFF = QUEUE_PRIORITY_STRIDE / 2


def make_queue_score(priority: int, sequence: int) -> float:
    """Encode strict priority ordering and FIFO ordering in one Redis score."""
    return int(priority) * QUEUE_PRIORITY_STRIDE - int(sequence)


class QueuePriority(int, Enum):
    """队列优先级"""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


@dataclass
class QueuedTask:
    """排队任务数据结构"""

    task_id: str
    priority: int
    created_at: float
    retry_count: int = 0
    max_retries: int = 3
    timeout: int = 3600  # 1小时超时

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueuedTask":
        allowed_fields = {field.name for field in fields(cls)}
        filtered = {key: value for key, value in data.items() if key in allowed_fields}
        return cls(**filtered)


class TaskLease(NamedTuple):
    """A queue claim whose token proves ownership of the processing slot."""

    task_id: str
    token: str


class RedisTaskQueue:
    """Redis任务队列管理器"""

    def __init__(
        self,
        redis_host: str = "redis",
        redis_port: int = 6378,
        redis_username: str = "",
        redis_password: str = "",
        redis_db: int = 1,  # 使用DB1避免与其他服务冲突
        queue_name: str = "document_parse_queue",
        max_concurrent_tasks: int = 10,
        visibility_timeout_seconds: float = 120.0,
    ):
        """
        初始化Redis任务队列

        Args:
            redis_host: Redis主机地址
            redis_port: Redis端口
            redis_username: Redis用户名
            redis_password: Redis密码
            redis_db: Redis数据库编号
            queue_name: 队列名称
            max_concurrent_tasks: 最大并发任务数
        """
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_username = redis_username
        self.redis_password = redis_password
        self.redis_db = redis_db
        self.queue_name = queue_name
        self.max_concurrent_tasks = max_concurrent_tasks
        if visibility_timeout_seconds <= 0:
            raise ValueError("visibility_timeout_seconds must be positive")
        self.visibility_timeout_seconds = float(visibility_timeout_seconds)

        # Redis键名
        self.queue_key = f"{queue_name}:queue"
        self.processing_key = f"{queue_name}:processing"
        self.completed_key = f"{queue_name}:completed"
        self.failed_key = f"{queue_name}:failed"
        self.task_data_key = f"{queue_name}:task_data"
        self.idempotency_key = f"{queue_name}:idempotency"
        self.leases_key = f"{queue_name}:leases"
        self.stats_key = f"{queue_name}:stats"
        self.sequence_key = f"{queue_name}:sequence"

        self.redis_client: Optional[redis.Redis] = None
        self._lock = asyncio.Lock()
        self._initialized = False

        self._enqueue_task_script = """
local queue_key = KEYS[1]
local task_data_key = KEYS[2]
local stats_key = KEYS[3]
local sequence_key = KEYS[4]
local task_id = ARGV[1]
local priority = tonumber(ARGV[2])
local task_data = ARGV[3]
local now = tonumber(ARGV[4])
local priority_stride = tonumber(ARGV[5])

local sequence = redis.call('INCR', sequence_key)
local score = (priority * priority_stride) - sequence
redis.call('ZADD', queue_key, score, task_id)
redis.call('HSET', task_data_key, task_id, task_data)
redis.call('HINCRBY', stats_key, 'total_enqueued', 1)
local queue_length = tonumber(redis.call('HGET', stats_key, 'queue_length') or '0')
redis.call('HSET', stats_key, 'queue_length', math.max(0, queue_length) + 1)
redis.call('HSET', stats_key, 'last_updated', now)

return sequence
"""

        self._claim_idempotent_task_script = """
local queue_key = KEYS[1]
local processing_key = KEYS[2]
local completed_key = KEYS[3]
local task_data_key = KEYS[4]
local idempotency_key = KEYS[5]
local stats_key = KEYS[6]
local sequence_key = KEYS[7]
local candidate_task_id = ARGV[1]
local request_key = ARGV[2]
local priority = tonumber(ARGV[3])
local task_data = ARGV[4]
local now = tonumber(ARGV[5])
local priority_stride = tonumber(ARGV[6])

local existing_task_id = redis.call('HGET', idempotency_key, request_key)
if existing_task_id then
    if redis.call('ZSCORE', queue_key, existing_task_id)
        or redis.call('ZSCORE', processing_key, existing_task_id)
        or redis.call('ZSCORE', completed_key, existing_task_id) then
        return {existing_task_id, '0'}
    end

    local raw_data = redis.call('HGET', task_data_key, existing_task_id)
    if raw_data then
        local decoded_ok, existing_data = pcall(cjson.decode, raw_data)
        if decoded_ok and existing_data then
            local status = tostring(existing_data['status'] or '')
            if status ~= 'failed' and status ~= 'cancelled' then
                return {existing_task_id, '0'}
            end
        end
    end
end

local sequence = redis.call('INCR', sequence_key)
local score = (priority * priority_stride) - sequence
redis.call('ZADD', queue_key, score, candidate_task_id)
redis.call('HSET', task_data_key, candidate_task_id, task_data)
redis.call('HSET', idempotency_key, request_key, candidate_task_id)
redis.call('HINCRBY', stats_key, 'total_enqueued', 1)
local queue_length = tonumber(redis.call('HGET', stats_key, 'queue_length') or '0')
redis.call('HSET', stats_key, 'queue_length', math.max(0, queue_length) + 1)
redis.call('HSET', stats_key, 'last_updated', now)

return {candidate_task_id, '1'}
"""

        self._dequeue_task_script = """
local queue_key = KEYS[1]
local processing_key = KEYS[2]
local leases_key = KEYS[3]
local stats_key = KEYS[4]
local max_concurrent = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local deadline = tonumber(ARGV[3])
local lease_token = ARGV[4]

local processing_count = redis.call('ZCARD', processing_key)
if processing_count >= max_concurrent then
    return nil
end

local result = redis.call('ZPOPMAX', queue_key, 1)
if (not result) or (#result == 0) then
    return nil
end

local task_id = result[1]
redis.call('ZADD', processing_key, deadline, task_id)
redis.call('HSET', leases_key, task_id, lease_token)
local processing = tonumber(redis.call('HGET', stats_key, 'current_processing') or '0')
redis.call('HSET', stats_key, 'current_processing', math.max(0, processing) + 1)
local queue_length = tonumber(redis.call('HGET', stats_key, 'queue_length') or '0')
redis.call('HSET', stats_key, 'queue_length', math.max(0, queue_length - 1))
redis.call('HSET', stats_key, 'last_updated', now)

return {task_id, lease_token}
"""

        self._heartbeat_task_script = """
local processing_key = KEYS[1]
local leases_key = KEYS[2]
local stats_key = KEYS[3]
local task_id = ARGV[1]
local lease_token = ARGV[2]
local now = tonumber(ARGV[3])
local deadline = tonumber(ARGV[4])

local current_token = redis.call('HGET', leases_key, task_id)
local current_deadline = redis.call('ZSCORE', processing_key, task_id)
if (not current_token) or current_token ~= lease_token or (not current_deadline) then
    return 0
end
if tonumber(current_deadline) <= now then
    return 0
end

redis.call('ZADD', processing_key, 'XX', deadline, task_id)
redis.call('HSET', stats_key, 'last_updated', now)
return 1
"""

        self._complete_task_script = """
local processing_key = KEYS[1]
local completed_key = KEYS[2]
local leases_key = KEYS[3]
local stats_key = KEYS[4]
local task_id = ARGV[1]
local lease_token = ARGV[2]
local now = tonumber(ARGV[3])

local current_token = redis.call('HGET', leases_key, task_id)
if (not current_token) or current_token ~= lease_token then
    return 0
end

local removed = redis.call('ZREM', processing_key, task_id)
if removed == 0 then
    return 0
end
redis.call('HDEL', leases_key, task_id)
local added = redis.call('ZADD', completed_key, 'NX', now, task_id)
local processing = tonumber(redis.call('HGET', stats_key, 'current_processing') or '0')
redis.call('HSET', stats_key, 'current_processing', math.max(0, processing - 1))
if added > 0 then
    redis.call('HINCRBY', stats_key, 'total_processed', 1)
end
redis.call('HSET', stats_key, 'last_updated', now)
return 1
"""

        self._cancel_processing_task_script = """
local processing_key = KEYS[1]
local leases_key = KEYS[2]
local stats_key = KEYS[3]
local task_id = ARGV[1]
local lease_token = ARGV[2]
local now = tonumber(ARGV[3])

local current_token = redis.call('HGET', leases_key, task_id)
if (not current_token) or current_token ~= lease_token then
    return 0
end

local removed = redis.call('ZREM', processing_key, task_id)
if removed == 0 then
    return 0
end
redis.call('HDEL', leases_key, task_id)
local processing = tonumber(redis.call('HGET', stats_key, 'current_processing') or '0')
redis.call('HSET', stats_key, 'current_processing', math.max(0, processing - 1))
redis.call('HSET', stats_key, 'last_updated', now)
return 1
"""

        self._transition_processing_task_script = """
local queue_key = KEYS[1]
local processing_key = KEYS[2]
local failed_key = KEYS[3]
local task_data_key = KEYS[4]
local leases_key = KEYS[5]
local stats_key = KEYS[6]
local sequence_key = KEYS[7]
local task_id = ARGV[1]
local lease_token = ARGV[2]
local now = tonumber(ARGV[3])
local message = ARGV[4]
local priority_stride = tonumber(ARGV[5])
local increment_retry = tonumber(ARGV[6])
local force_terminal = tonumber(ARGV[7])
local require_stale = tonumber(ARGV[8])
local completed_at = ARGV[9]

local current_token = redis.call('HGET', leases_key, task_id)
if current_token then
    if current_token ~= lease_token then
        return 'lease_mismatch'
    end
elseif lease_token ~= '' then
    return 'lease_mismatch'
elseif require_stale == 0 then
    return 'lease_mismatch'
end

local current_deadline = redis.call('ZSCORE', processing_key, task_id)
if not current_deadline then
    return 'not_processing'
end
if require_stale == 1 and tonumber(current_deadline) > now then
    return 'not_stale'
end

local raw_data = redis.call('HGET', task_data_key, task_id)
local decoded_ok, task_data = pcall(cjson.decode, raw_data or '')
if (not decoded_ok) or (not task_data) then
    force_terminal = 1
    task_data = nil
end
if task_data and increment_retry == 1 and require_stale == 0
    and type(task_data['message']) == 'string' then
    message = task_data['message']
end

local should_retry = false
if force_terminal == 0 and task_data then
    if increment_retry == 0 then
        should_retry = true
    else
        local retry_count = tonumber(task_data['retry_count']) or 0
        local max_retries = tonumber(task_data['max_retries']) or 3
        should_retry = retry_count < max_retries
    end
end

local removed = redis.call('ZREM', processing_key, task_id)
if removed == 0 then
    return 'not_processing'
end
redis.call('HDEL', leases_key, task_id)
local processing = tonumber(redis.call('HGET', stats_key, 'current_processing') or '0')
redis.call('HSET', stats_key, 'current_processing', math.max(0, processing - 1))

if should_retry then
    local priority = tonumber(task_data['priority']) or 2
    if increment_retry == 1 then
        task_data['retry_count'] = (tonumber(task_data['retry_count']) or 0) + 1
        priority = math.max(1, priority - 1)
        task_data['priority'] = priority
    end
    task_data['status'] = 'queued'
    task_data['message'] = message
    task_data['progress'] = 0
    task_data['started_at'] = cjson.null
    task_data['completed_at'] = cjson.null
    redis.call('HSET', task_data_key, task_id, cjson.encode(task_data))

    local sequence = redis.call('INCR', sequence_key)
    local score = (priority * priority_stride) - sequence
    local added = redis.call('ZADD', queue_key, 'NX', score, task_id)
    if added > 0 then
        local queue_length = tonumber(redis.call('HGET', stats_key, 'queue_length') or '0')
        redis.call('HSET', stats_key, 'queue_length', math.max(0, queue_length) + 1)
    end
    redis.call('HSET', stats_key, 'last_updated', now)
    return 'requeued'
end

if task_data then
    task_data['status'] = 'failed'
    task_data['message'] = message
    task_data['completed_at'] = completed_at
    redis.call('HSET', task_data_key, task_id, cjson.encode(task_data))
end
local failed_added = redis.call('ZADD', failed_key, 'NX', now, task_id)
if failed_added > 0 then
    redis.call('HINCRBY', stats_key, 'total_failed', 1)
end
redis.call('HSET', stats_key, 'last_updated', now)
return 'failed'
"""

        self._update_leased_task_data_script = """
local processing_key = KEYS[1]
local leases_key = KEYS[2]
local task_data_key = KEYS[3]
local task_id = ARGV[1]
local lease_token = ARGV[2]
local replacement_json = ARGV[3]

local current_token = redis.call('HGET', leases_key, task_id)
if (not current_token) or current_token ~= lease_token
    or (not redis.call('ZSCORE', processing_key, task_id)) then
    return nil
end

local replacement = cjson.decode(replacement_json)
local existing_json = redis.call('HGET', task_data_key, task_id)
if existing_json then
    local decoded_ok, existing = pcall(cjson.decode, existing_json)
    if decoded_ok and existing and existing['cancel_requested'] == true then
        replacement['cancel_requested'] = true
    end
end
local persisted = cjson.encode(replacement)
redis.call('HSET', task_data_key, task_id, persisted)
return persisted
"""

        self._migrate_queue_scores_script = """
local queue_key = KEYS[1]
local task_data_key = KEYS[2]
local sequence_key = KEYS[3]
local priority_stride = tonumber(ARGV[1])
local legacy_cutoff = tonumber(ARGV[2])
local normal_priority = tonumber(ARGV[3])
local migrated = 0

local entries = redis.call('ZRANGE', queue_key, 0, -1, 'WITHSCORES')
for index = 1, #entries, 2 do
    local task_id = entries[index]
    local score = tonumber(entries[index + 1])
    if score < legacy_cutoff then
        local priority = normal_priority
        local raw_data = redis.call('HGET', task_data_key, task_id)
        if raw_data then
            local decoded_ok, task_data = pcall(cjson.decode, raw_data)
            if decoded_ok and task_data then
                priority = tonumber(task_data['priority']) or normal_priority
            end
        end

        local sequence = redis.call('INCR', sequence_key)
        redis.call('ZADD', queue_key, 'XX', (priority * priority_stride) - sequence, task_id)
        migrated = migrated + 1
    end
end

return migrated
"""

        self._remove_queued_task_script = """
local queue_key = KEYS[1]
local stats_key = KEYS[2]
local task_id = ARGV[1]
local now = tonumber(ARGV[2])

local removed = redis.call('ZREM', queue_key, task_id)
if removed > 0 then
    local queue_length = tonumber(redis.call('HGET', stats_key, 'queue_length') or '0')
    redis.call('HSET', stats_key, 'queue_length', math.max(0, queue_length - 1))
    redis.call('HSET', stats_key, 'last_updated', now)
end

return removed
"""

    async def initialize(self) -> bool:
        """初始化Redis连接"""
        if self._initialized:
            return True

        try:
            # 构建Redis URL
            auth = ""
            if self.redis_username or self.redis_password:
                username = quote(self.redis_username, safe="")
                password = quote(self.redis_password, safe="")
                auth = f"{username}:{password}@"
            redis_url = (
                f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"
            )

            self.redis_client = redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=30,
                socket_connect_timeout=10,
                retry_on_timeout=True,
                health_check_interval=30,
            )

            # 测试连接
            await self.redis_client.ping()
            self._initialized = True

            migrated = await self.redis_client.eval(
                self._migrate_queue_scores_script,
                3,
                self.queue_key,
                self.task_data_key,
                self.sequence_key,
                QUEUE_PRIORITY_STRIDE,
                LEGACY_QUEUE_SCORE_CUTOFF,
                QueuePriority.NORMAL.value,
            )
            if migrated:
                logger.info("已迁移 %s 个旧格式队列分数", migrated)

            logger.info(
                f"Redis任务队列初始化成功: {self.redis_host}:{self.redis_port}/{self.redis_db}"
            )

            # 初始化统计信息
            await self._init_stats()

            return True

        except Exception as error:
            self._initialized = False
            _log_queue_failure(error)
            return False

    async def _init_stats(self):
        """初始化统计信息"""
        stats = {
            "total_enqueued": 0,
            "total_processed": 0,
            "total_failed": 0,
            "current_processing": await self.redis_client.zcard(self.processing_key),
            "queue_length": await self.redis_client.zcard(self.queue_key),
            "last_updated": time.time(),
        }

        # 只在统计信息不存在时初始化
        if not await self.redis_client.exists(self.stats_key):
            await self.redis_client.hset(self.stats_key, mapping=stats)

    async def enqueue_task(
        self,
        task_id: str,
        priority: QueuePriority = QueuePriority.NORMAL,
        task_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        将任务加入队列

        Args:
            task_id: 任务ID
            priority: 任务优先级

        Returns:
            是否成功加入队列
        """
        if not self._initialized:
            await self.initialize()

        try:
            # 创建排队任务
            created_at = time.time()
            queued_task = QueuedTask(
                task_id=task_id,
                priority=priority.value,
                created_at=created_at,
            )
            persisted_task_data = queued_task.to_dict()
            if task_data:
                persisted_task_data.update(task_data)
            persisted_task_data = sanitize_task_metadata(persisted_task_data)

            await self.redis_client.eval(
                self._enqueue_task_script,
                4,
                self.queue_key,
                self.task_data_key,
                self.stats_key,
                self.sequence_key,
                task_id,
                priority.value,
                json.dumps(persisted_task_data),
                created_at,
                QUEUE_PRIORITY_STRIDE,
            )

            logger.info(f"任务 {task_id} 已加入队列，优先级: {priority.name}")
            return True

        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def claim_idempotent_task(
        self,
        task_id: str,
        request_key: str,
        priority: QueuePriority = QueuePriority.NORMAL,
        task_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, bool]:
        """Atomically enqueue a task or return the active/completed prior claim."""
        if not self._initialized and not await self.initialize():
            raise RuntimeError("Redis task queue initialization failed")

        created_at = time.time()
        queued_task = QueuedTask(
            task_id=task_id,
            priority=priority.value,
            created_at=created_at,
        )
        persisted_task_data = queued_task.to_dict()
        if task_data:
            persisted_task_data.update(task_data)
        persisted_task_data = sanitize_task_metadata(persisted_task_data)

        result = await self.redis_client.eval(
            self._claim_idempotent_task_script,
            7,
            self.queue_key,
            self.processing_key,
            self.completed_key,
            self.task_data_key,
            self.idempotency_key,
            self.stats_key,
            self.sequence_key,
            task_id,
            request_key,
            priority.value,
            json.dumps(persisted_task_data),
            created_at,
            QUEUE_PRIORITY_STRIDE,
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise RuntimeError("Redis returned an invalid idempotency claim result")

        resolved_task_id = str(result[0])
        created = str(result[1]) == "1"
        logger.info(
            "Idempotent task claim resolved task=%s created=%s",
            resolved_task_id,
            created,
        )
        return resolved_task_id, created

    async def dequeue_task(self) -> Optional[TaskLease]:
        """Atomically claim the next task and return its unique lease token."""
        if not self._initialized and not await self.initialize():
            return None

        try:
            now = time.time()
            lease_token = secrets.token_urlsafe(32)
            result = await self.redis_client.eval(
                self._dequeue_task_script,
                4,
                self.queue_key,
                self.processing_key,
                self.leases_key,
                self.stats_key,
                self.max_concurrent_tasks,
                now,
                now + self.visibility_timeout_seconds,
                lease_token,
            )
            if not result:
                return None
            if not isinstance(result, (list, tuple)) or len(result) != 2:
                raise RuntimeError("Redis returned an invalid task lease")

            lease = TaskLease(str(result[0]), str(result[1]))
            logger.info("任务 %s 已取得处理 lease", lease.task_id)
            return lease
        except Exception as error:
            _log_queue_failure(error)
            return None

    async def heartbeat_task(self, task_id: str, lease_token: str) -> bool:
        """Extend a live lease if ``lease_token`` is still its current owner."""
        if not self._initialized and not await self.initialize():
            return False

        try:
            now = time.time()
            refreshed = await self.redis_client.eval(
                self._heartbeat_task_script,
                3,
                self.processing_key,
                self.leases_key,
                self.stats_key,
                task_id,
                lease_token,
                now,
                now + self.visibility_timeout_seconds,
            )
            return bool(refreshed)
        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def complete_task(self, task_id: str, lease_token: str = "") -> bool:
        """Commit completion only when the caller owns the current lease."""
        if not self._initialized and not await self.initialize():
            return False

        try:
            completed = await self.redis_client.eval(
                self._complete_task_script,
                4,
                self.processing_key,
                self.completed_key,
                self.leases_key,
                self.stats_key,
                task_id,
                lease_token,
                time.time(),
            )
            if completed:
                logger.info("任务 %s 已标记为完成", task_id)
            return bool(completed)
        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def _transition_processing_task(
        self,
        task_id: str,
        lease_token: str,
        message: str,
        *,
        increment_retry: bool,
        force_terminal: bool,
        require_stale: bool,
    ) -> str:
        if not self._initialized and not await self.initialize():
            return "unavailable"

        sanitized_message = sanitize_task_metadata({"message": str(message)}).get(
            "message", "Task processing failed"
        )
        now = time.time()
        try:
            result = await self.redis_client.eval(
                self._transition_processing_task_script,
                7,
                self.queue_key,
                self.processing_key,
                self.failed_key,
                self.task_data_key,
                self.leases_key,
                self.stats_key,
                self.sequence_key,
                task_id,
                lease_token,
                now,
                sanitized_message,
                QUEUE_PRIORITY_STRIDE,
                int(increment_retry),
                int(force_terminal),
                int(require_stale),
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            )
            return str(result)
        except Exception as error:
            _log_queue_failure(error, task_id)
            return "unavailable"

    async def fail_task_with_result(
        self,
        task_id: str,
        lease_token: str,
        error_message: str = "",
    ) -> str:
        """Retry or terminally fail a task, returning its resulting disposition."""
        result = await self._transition_processing_task(
            task_id,
            lease_token,
            error_message or "Task processing failed",
            increment_retry=True,
            force_terminal=False,
            require_stale=False,
        )
        if result == "requeued":
            logger.info("任务 %s 失败并已按重试策略重新排队", task_id)
        elif result == "failed":
            logger.error("任务 %s 已达到重试上限", task_id)
        return result

    async def fail_task(
        self,
        task_id: str,
        error_message: str = "",
        lease_token: str = "",
    ) -> bool:
        """Compatibility wrapper returning whether a failure transition committed."""
        result = await self.fail_task_with_result(task_id, lease_token, error_message)
        return result in {"requeued", "failed"}

    async def requeue_task(
        self,
        task_id: str,
        lease_token: str,
        reason: str = "Worker shutdown; task returned to queue",
        *,
        payload_available: bool = True,
    ) -> str:
        """Return an owned task to the queue without consuming a retry attempt."""
        return await self._transition_processing_task(
            task_id,
            lease_token,
            reason,
            increment_retry=False,
            force_terminal=not payload_available,
            require_stale=False,
        )

    async def get_stale_task_leases(
        self, now: Optional[float] = None
    ) -> list[TaskLease]:
        """List expired processing claims; transitions still recheck them atomically."""
        if not self._initialized and not await self.initialize():
            return []

        cutoff = time.time() if now is None else now
        try:
            task_ids = await self.redis_client.zrangebyscore(
                self.processing_key, "-inf", cutoff
            )
            if not task_ids:
                return []
            tokens = await self.redis_client.hmget(self.leases_key, task_ids)
            return [
                TaskLease(str(task_id), str(token or ""))
                for task_id, token in zip(task_ids, tokens, strict=True)
            ]
        except Exception as error:
            _log_queue_failure(error)
            return []

    async def recover_stale_task(
        self,
        task_id: str,
        lease_token: str,
        *,
        payload_available: bool,
    ) -> str:
        """Atomically retry or fail an expired lease according to retry policy."""
        message = (
            "Processing lease expired; task returned to queue"
            if payload_available
            else "Processing lease expired and task payload is missing"
        )
        return await self._transition_processing_task(
            task_id,
            lease_token,
            message,
            increment_retry=True,
            force_terminal=not payload_available,
            require_stale=True,
        )

    async def get_queue_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        if not self._initialized:
            await self.initialize()

        try:
            # 获取实时统计
            queue_length = await self.redis_client.zcard(self.queue_key)
            processing_count = await self.redis_client.zcard(self.processing_key)
            completed_count = await self.redis_client.zcard(self.completed_key)
            failed_count = await self.redis_client.zcard(self.failed_key)

            # 获取存储的统计信息
            stored_stats = await self.redis_client.hgetall(self.stats_key)

            return {
                "queue_length": queue_length,
                "processing_count": processing_count,
                "completed_count": completed_count,
                "failed_count": failed_count,
                "total_enqueued": int(stored_stats.get("total_enqueued", 0)),
                "total_processed": int(stored_stats.get("total_processed", 0)),
                "total_failed": int(stored_stats.get("total_failed", 0)),
                "max_concurrent_tasks": self.max_concurrent_tasks,
                "last_updated": float(stored_stats.get("last_updated", 0)),
            }

        except Exception as error:
            _log_queue_failure(error)
            return {}

    async def get_task_position(self, task_id: str) -> Optional[int]:
        """获取任务在队列中的位置"""
        if not self._initialized:
            await self.initialize()

        try:
            # 获取任务在队列中的排名（从0开始）
            rank = await self.redis_client.zrevrank(self.queue_key, task_id)
            return rank + 1 if rank is not None else None

        except Exception as error:
            _log_queue_failure(error, task_id)
            return None

    async def remove_queued_task(self, task_id: str) -> bool:
        """从排队集合中移除任务"""
        if not self._initialized:
            await self.initialize()

        try:
            removed = await self.redis_client.eval(
                self._remove_queued_task_script,
                2,
                self.queue_key,
                self.stats_key,
                task_id,
                time.time(),
            )

            if removed:
                logger.info(f"任务 {task_id} 已从排队队列移除")

            return bool(removed)

        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def mark_task_cancelled(self, task_id: str, lease_token: str = "") -> bool:
        """Remove a cancelled processing task only for its current lease owner."""
        if not self._initialized and not await self.initialize():
            return False

        try:
            removed = await self.redis_client.eval(
                self._cancel_processing_task_script,
                3,
                self.processing_key,
                self.leases_key,
                self.stats_key,
                task_id,
                lease_token,
                time.time(),
            )
            return bool(removed)
        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def mark_leased_task_cancelled(self, task_id: str, lease_token: str) -> bool:
        """Explicitly named alias for callers migrating to the lease protocol."""
        return await self.mark_task_cancelled(task_id, lease_token)

    async def is_task_processing(self, task_id: str) -> bool:
        """检查任务是否在处理中集合中"""
        if not self._initialized:
            await self.initialize()

        try:
            return (
                await self.redis_client.zscore(self.processing_key, task_id) is not None
            )
        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def get_task_data(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            task_data = await self.redis_client.hget(self.task_data_key, task_id)
            if not task_data:
                return None
            return json.loads(task_data)
        except Exception as error:
            _log_queue_failure(error, task_id)
            return None

    async def set_task_data(self, task_id: str, task_data: Dict[str, Any]) -> bool:
        """覆盖写入任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            async with self._lock:
                sanitized = sanitize_task_metadata(task_data)
                await self.redis_client.hset(
                    self.task_data_key, task_id, json.dumps(sanitized)
                )
            return True
        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def update_task_data(
        self,
        task_id: str,
        updates: Dict[str, Any],
        lease_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """合并更新任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            if lease_token is not None:
                sanitized = sanitize_task_metadata(updates)
                persisted = await self.redis_client.eval(
                    self._update_leased_task_data_script,
                    3,
                    self.processing_key,
                    self.leases_key,
                    self.task_data_key,
                    task_id,
                    lease_token,
                    json.dumps(sanitized),
                )
                return json.loads(persisted) if persisted else None

            async with self._lock:
                existing = await self.redis_client.hget(self.task_data_key, task_id)
                if not existing:
                    return None
                task_data = json.loads(existing)
                task_data.update(updates)
                sanitized = sanitize_task_metadata(task_data)
                await self.redis_client.hset(
                    self.task_data_key, task_id, json.dumps(sanitized)
                )
                return sanitized
        except Exception as error:
            _log_queue_failure(error, task_id)
            return None

    async def delete_task_data(self, task_id: str) -> bool:
        """删除任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            await self.redis_client.hdel(self.task_data_key, task_id)
            return True
        except Exception as error:
            _log_queue_failure(error, task_id)
            return False

    async def list_task_data(self) -> Dict[str, Dict[str, Any]]:
        """列出全部任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            raw_data = await self.redis_client.hgetall(self.task_data_key)
            return {
                task_id: json.loads(task_json)
                for task_id, task_json in raw_data.items()
            }
        except Exception as error:
            _log_queue_failure(error)
            return {}

    async def cleanup_expired_tasks(self, timeout_hours: int = 24) -> int:
        """清理过期任务"""
        if not self._initialized:
            await self.initialize()

        try:
            cutoff_time = time.time() - (timeout_hours * 3600)
            cleaned_count = 0

            # 清理完成的任务
            completed_cleaned = await self.redis_client.zremrangebyscore(
                self.completed_key, 0, cutoff_time
            )

            # 清理失败的任务
            failed_cleaned = await self.redis_client.zremrangebyscore(
                self.failed_key, 0, cutoff_time
            )

            # Processing leases are recovered by DocumentParseService, which can
            # verify that the payload still exists before deciding to retry.
            cleaned_count = completed_cleaned + failed_cleaned

            if cleaned_count > 0:
                logger.info(f"清理了 {cleaned_count} 个过期任务")

            return cleaned_count

        except Exception as error:
            _log_queue_failure(error)
            return 0

    async def close(self):
        """关闭Redis连接"""
        if self.redis_client:
            await self.redis_client.close()
            self._initialized = False
            logger.info("Redis任务队列连接已关闭")
