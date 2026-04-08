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
import time
from dataclasses import asdict, dataclass, fields
from typing import Dict, Optional, Any
from enum import Enum

import redis.asyncio as redis

logger = logging.getLogger(__name__)


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
    def from_dict(cls, data: Dict[str, Any]) -> 'QueuedTask':
        allowed_fields = {field.name for field in fields(cls)}
        filtered = {key: value for key, value in data.items() if key in allowed_fields}
        return cls(**filtered)


class RedisTaskQueue:
    """Redis任务队列管理器"""
    
    def __init__(self, 
                 redis_host: str = "redis",
                 redis_port: int = 6378,
                 redis_username: str = "wangyue",
                 redis_password: str = "wangyue123",
                 redis_db: int = 1,  # 使用DB1避免与其他服务冲突
                 queue_name: str = "document_parse_queue",
                 max_concurrent_tasks: int = 10):
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
        
        # Redis键名
        self.queue_key = f"{queue_name}:queue"
        self.processing_key = f"{queue_name}:processing"
        self.completed_key = f"{queue_name}:completed"
        self.failed_key = f"{queue_name}:failed"
        self.task_data_key = f"{queue_name}:task_data"
        self.stats_key = f"{queue_name}:stats"
        
        self.redis_client: Optional[redis.Redis] = None
        self._lock = asyncio.Lock()
        self._initialized = False

        self._dequeue_task_script = """
local queue_key = KEYS[1]
local processing_key = KEYS[2]
local stats_key = KEYS[3]
local max_concurrent = tonumber(ARGV[1])
local now = tonumber(ARGV[2])

local processing_count = redis.call('ZCARD', processing_key)
if processing_count >= max_concurrent then
    return nil
end

local result = redis.call('ZPOPMAX', queue_key, 1)
if (not result) or (#result == 0) then
    return nil
end

local task_id = result[1]
redis.call('ZADD', processing_key, now, task_id)
redis.call('HINCRBY', stats_key, 'current_processing', 1)
redis.call('HINCRBY', stats_key, 'queue_length', -1)
redis.call('HSET', stats_key, 'last_updated', now)

return task_id
"""

        self._remove_queued_task_script = """
local queue_key = KEYS[1]
local stats_key = KEYS[2]
local task_id = ARGV[1]
local now = tonumber(ARGV[2])

local removed = redis.call('ZREM', queue_key, task_id)
if removed > 0 then
    redis.call('HINCRBY', stats_key, 'queue_length', -1)
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
            redis_url = f"redis://{self.redis_username}:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
            
            self.redis_client = redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=30,
                socket_connect_timeout=10,
                retry_on_timeout=True,
                health_check_interval=30
            )
            
            # 测试连接
            await self.redis_client.ping()
            self._initialized = True
            
            logger.info(f"Redis任务队列初始化成功: {self.redis_host}:{self.redis_port}/{self.redis_db}")
            
            # 初始化统计信息
            await self._init_stats()
            
            return True
            
        except Exception as e:
            logger.error(f"Redis任务队列初始化失败: {e}")
            return False
    
    async def _init_stats(self):
        """初始化统计信息"""
        stats = {
            "total_enqueued": 0,
            "total_processed": 0,
            "total_failed": 0,
            "current_processing": 0,
            "queue_length": 0,
            "last_updated": time.time()
        }
        
        # 只在统计信息不存在时初始化
        if not await self.redis_client.exists(self.stats_key):
            await self.redis_client.hset(self.stats_key, mapping=stats)
    
    async def enqueue_task(
        self,
        task_id: str,
        priority: QueuePriority = QueuePriority.NORMAL,
        task_data: Optional[Dict[str, Any]] = None
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
            queued_task = QueuedTask(
                task_id=task_id,
                priority=priority.value,
                created_at=time.time()
            )
            persisted_task_data = queued_task.to_dict()
            if task_data:
                persisted_task_data.update(task_data)
            
            # 使用Redis事务确保原子性
            async with self.redis_client.pipeline(transaction=True) as pipe:
                # 添加到优先级队列（使用sorted set，分数为优先级+时间戳）
                score = priority.value * 1000000 + time.time()
                await pipe.zadd(self.queue_key, {task_id: score})
                
                # 保存任务数据
                await pipe.hset(self.task_data_key, task_id, json.dumps(persisted_task_data))
                
                # 更新统计信息
                await pipe.hincrby(self.stats_key, "total_enqueued", 1)
                await pipe.hincrby(self.stats_key, "queue_length", 1)
                await pipe.hset(self.stats_key, "last_updated", time.time())
                
                await pipe.execute()
            
            logger.info(f"任务 {task_id} 已加入队列，优先级: {priority.name}")
            return True
            
        except Exception as e:
            logger.error(f"任务 {task_id} 加入队列失败: {e}")
            return False
    
    async def dequeue_task(self) -> Optional[str]:
        """
        从队列中取出一个任务
        
        Returns:
            任务ID，如果队列为空则返回None
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            task_id = await self.redis_client.eval(
                self._dequeue_task_script,
                3,
                self.queue_key,
                self.processing_key,
                self.stats_key,
                self.max_concurrent_tasks,
                time.time(),
            )
            if not task_id:
                return None
            
            logger.info(f"任务 {task_id} 已从队列中取出开始处理")
            return task_id
            
        except Exception as e:
            logger.error(f"从队列取出任务失败: {e}")
            return None
    
    async def complete_task(self, task_id: str) -> bool:
        """
        标记任务为完成
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功标记
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            async with self.redis_client.pipeline(transaction=True) as pipe:
                # 从处理中队列移除
                await pipe.zrem(self.processing_key, task_id)
                
                # 添加到完成队列
                await pipe.zadd(self.completed_key, {task_id: time.time()})
                
                # 更新统计信息
                await pipe.hincrby(self.stats_key, "current_processing", -1)
                await pipe.hincrby(self.stats_key, "total_processed", 1)
                await pipe.hset(self.stats_key, "last_updated", time.time())
                
                await pipe.execute()
            
            logger.info(f"任务 {task_id} 已标记为完成")
            return True
            
        except Exception as e:
            logger.error(f"标记任务 {task_id} 完成失败: {e}")
            return False
    
    async def fail_task(self, task_id: str, error_message: str = "") -> bool:
        """
        标记任务为失败
        
        Args:
            task_id: 任务ID
            error_message: 错误信息
            
        Returns:
            是否成功标记
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            # 获取任务数据
            task_data_str = await self.redis_client.hget(self.task_data_key, task_id)
            if not task_data_str:
                return False
            
            task_data = json.loads(task_data_str)
            queued_task = QueuedTask.from_dict(task_data)
            
            # 检查是否需要重试
            if queued_task.retry_count < queued_task.max_retries:
                # 重新加入队列
                queued_task.retry_count += 1
                task_data["retry_count"] = queued_task.retry_count
                await self.redis_client.hset(self.task_data_key, task_id, json.dumps(task_data))
                
                # 重新加入队列（降低优先级）
                score = (queued_task.priority - 1) * 1000000 + time.time()
                async with self.redis_client.pipeline(transaction=True) as pipe:
                    await pipe.zadd(self.queue_key, {task_id: score})
                    await pipe.hincrby(self.stats_key, "queue_length", 1)
                    await pipe.hset(self.stats_key, "last_updated", time.time())
                    await pipe.execute()
                
                logger.info(f"任务 {task_id} 失败，重试第 {queued_task.retry_count} 次")
            else:
                # 超过重试次数，标记为最终失败
                async with self.redis_client.pipeline(transaction=True) as pipe:
                    await pipe.zadd(self.failed_key, {task_id: time.time()})
                    await pipe.hincrby(self.stats_key, "total_failed", 1)
                    await pipe.execute()
                
                logger.error(f"任务 {task_id} 最终失败: {error_message}")
            
            # 从处理中队列移除
            async with self.redis_client.pipeline(transaction=True) as pipe:
                await pipe.zrem(self.processing_key, task_id)
                await pipe.hincrby(self.stats_key, "current_processing", -1)
                await pipe.hset(self.stats_key, "last_updated", time.time())
                await pipe.execute()
            
            return True

        except Exception as e:
            logger.error(f"标记任务 {task_id} 失败时出错: {e}")
            return False

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
                "last_updated": float(stored_stats.get("last_updated", 0))
            }

        except Exception as e:
            logger.error(f"获取队列统计信息失败: {e}")
            return {}

    async def get_task_position(self, task_id: str) -> Optional[int]:
        """获取任务在队列中的位置"""
        if not self._initialized:
            await self.initialize()

        try:
            # 获取任务在队列中的排名（从0开始）
            rank = await self.redis_client.zrevrank(self.queue_key, task_id)
            return rank + 1 if rank is not None else None

        except Exception as e:
            logger.error(f"获取任务 {task_id} 位置失败: {e}")
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

        except Exception as e:
            logger.error(f"移除排队任务 {task_id} 失败: {e}")
            return False

    async def mark_task_cancelled(self, task_id: str) -> bool:
        """将处理中任务标记为已取消并移出处理中集合"""
        if not self._initialized:
            await self.initialize()

        try:
            removed = await self.redis_client.zrem(self.processing_key, task_id)
            if removed:
                async with self.redis_client.pipeline(transaction=True) as pipe:
                    await pipe.hincrby(self.stats_key, "current_processing", -1)
                    await pipe.hset(self.stats_key, "last_updated", time.time())
                    await pipe.execute()
            return bool(removed)
        except Exception as e:
            logger.error(f"标记任务 {task_id} 已取消失败: {e}")
            return False

    async def is_task_processing(self, task_id: str) -> bool:
        """检查任务是否在处理中集合中"""
        if not self._initialized:
            await self.initialize()

        try:
            return bool(await self.redis_client.zscore(self.processing_key, task_id))
        except Exception as e:
            logger.error(f"检查任务 {task_id} 处理状态失败: {e}")
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
        except Exception as e:
            logger.error(f"读取任务 {task_id} 元数据失败: {e}")
            return None

    async def set_task_data(self, task_id: str, task_data: Dict[str, Any]) -> bool:
        """覆盖写入任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            async with self._lock:
                await self.redis_client.hset(self.task_data_key, task_id, json.dumps(task_data))
            return True
        except Exception as e:
            logger.error(f"写入任务 {task_id} 元数据失败: {e}")
            return False

    async def update_task_data(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """合并更新任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            async with self._lock:
                existing = await self.redis_client.hget(self.task_data_key, task_id)
                if not existing:
                    return None
                task_data = json.loads(existing)
                task_data.update(updates)
                await self.redis_client.hset(self.task_data_key, task_id, json.dumps(task_data))
                return task_data
        except Exception as e:
            logger.error(f"更新任务 {task_id} 元数据失败: {e}")
            return None

    async def delete_task_data(self, task_id: str) -> bool:
        """删除任务元数据"""
        if not self._initialized:
            await self.initialize()

        try:
            await self.redis_client.hdel(self.task_data_key, task_id)
            return True
        except Exception as e:
            logger.error(f"删除任务 {task_id} 元数据失败: {e}")
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
        except Exception as e:
            logger.error(f"列出任务元数据失败: {e}")
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

            # 清理超时的处理中任务
            timeout_cutoff = time.time() - 3600  # 1小时超时
            timeout_tasks = await self.redis_client.zrangebyscore(
                self.processing_key, 0, timeout_cutoff
            )

            for task_id in timeout_tasks:
                await self.fail_task(task_id, "任务处理超时")

            cleaned_count = completed_cleaned + failed_cleaned + len(timeout_tasks)

            if cleaned_count > 0:
                logger.info(f"清理了 {cleaned_count} 个过期任务")

            return cleaned_count

        except Exception as e:
            logger.error(f"清理过期任务失败: {e}")
            return 0

    async def close(self):
        """关闭Redis连接"""
        if self.redis_client:
            await self.redis_client.close()
            self._initialized = False
            logger.info("Redis任务队列连接已关闭")
