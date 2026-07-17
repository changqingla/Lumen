"""带防抖机制的记忆更新队列。"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.agents.memory.scope import (
    normalize_agent_name,
    normalize_memory_scope,
)
from src.config.memory_config import get_memory_config

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """待处理记忆更新的一段会话上下文。"""

    thread_id: str
    messages: list[Any]
    memory_scope: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_name: str | None = None


class MemoryUpdateQueue:
    """记忆更新任务队列。

    队列会收集会话上下文，并在可配置的防抖时间后统一处理。
    防抖窗口内到达的多条会话会被批处理。
    """

    def __init__(self):
        """初始化记忆更新队列。"""
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False

    def add(
        self,
        thread_id: str,
        messages: list[Any],
        *,
        memory_scope: str,
        agent_name: str | None = None,
    ) -> None:
        """向队列添加一条会话更新任务。

        参数：
            thread_id: 线程 ID。
            messages: 会话消息列表。
            memory_scope: Required Backend-issued tenant partition.
            agent_name: Optional agent subpartition within that scope.
        """
        config = get_memory_config()
        if not config.enabled:
            return

        scope = normalize_memory_scope(memory_scope)
        normalized_agent = normalize_agent_name(agent_name)
        context = ConversationContext(
            thread_id=thread_id,
            messages=messages,
            memory_scope=scope,
            agent_name=normalized_agent,
        )

        with self._lock:
            # Debounce only within the exact tenant/agent/thread partition.
            # Reusing a thread ID in another tenant must never evict its task.
            self._queue = [
                queued
                for queued in self._queue
                if (
                    queued.memory_scope,
                    queued.agent_name,
                    queued.thread_id,
                )
                != (scope, normalized_agent, thread_id)
            ]
            self._queue.append(context)

            # 重置或启动防抖计时器
            self._reset_timer()

        logger.info(
            "Memory update queued, queue size: %s",
            len(self._queue),
        )

    def _reset_timer(self) -> None:
        """重置防抖计时器。"""
        config = get_memory_config()

        # 若已有计时器则先取消
        if self._timer is not None:
            self._timer.cancel()

        # 启动新的计时器
        self._timer = threading.Timer(
            config.debounce_seconds,
            self._process_queue,
        )
        self._timer.daemon = True
        self._timer.start()

        logger.info("Memory update timer set for %ss", config.debounce_seconds)

    def _process_queue(self) -> None:
        """处理当前队列中的全部会话上下文。"""
        # 在函数内导入以避免循环依赖
        from src.agents.memory.updater import MemoryUpdater

        with self._lock:
            if self._processing:
                # 已在处理，重新设置计时器稍后再试
                self._reset_timer()
                return

            if not self._queue:
                return

            self._processing = True
            contexts_to_process = self._queue.copy()
            self._queue.clear()
            self._timer = None

        logger.info("Processing %s queued memory updates", len(contexts_to_process))

        try:
            updater = MemoryUpdater()

            for context in contexts_to_process:
                try:
                    logger.info("Updating queued memory")
                    success = updater.update_memory(
                        messages=context.messages,
                        memory_scope=context.memory_scope,
                        thread_id=context.thread_id,
                        agent_name=context.agent_name,
                    )
                    if success:
                        logger.info("Memory updated successfully")
                    else:
                        logger.warning("Memory update skipped or failed")
                except Exception as exc:
                    logger.error(
                        "Error updating memory (%s)",
                        type(exc).__name__,
                    )

                # 多任务批处理时小幅延迟，降低触发限流概率
                if len(contexts_to_process) > 1:
                    time.sleep(0.5)

        finally:
            with self._lock:
                self._processing = False

    def clear(self) -> None:
        """清空队列并重置处理状态。

        主要用于测试场景。
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._queue.clear()
            self._processing = False


# 全局单例队列实例
_memory_queue: MemoryUpdateQueue | None = None
_queue_lock = threading.Lock()


def get_memory_queue() -> MemoryUpdateQueue:
    """获取记忆更新队列单例。"""
    global _memory_queue
    with _queue_lock:
        if _memory_queue is None:
            _memory_queue = MemoryUpdateQueue()
        return _memory_queue
