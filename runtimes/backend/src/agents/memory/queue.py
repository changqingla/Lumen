"""带防抖机制的记忆更新队列。"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.config.memory_config import get_memory_config


@dataclass
class ConversationContext:
    """待处理记忆更新的一段会话上下文。"""

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
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

    def add(self, thread_id: str, messages: list[Any], agent_name: str | None = None) -> None:
        """向队列添加一条会话更新任务。

        参数：
            thread_id: 线程 ID。
            messages: 会话消息列表。
            agent_name: 若提供则按 agent 维度存储记忆；否则使用全局记忆。
        """
        config = get_memory_config()
        if not config.enabled:
            return

        context = ConversationContext(
            thread_id=thread_id,
            messages=messages,
            agent_name=agent_name,
        )

        with self._lock:
            # 若该线程已有待处理任务，用最新一条覆盖旧任务
            self._queue = [c for c in self._queue if c.thread_id != thread_id]
            self._queue.append(context)

            # 重置或启动防抖计时器
            self._reset_timer()

        print(f"Memory update queued for thread {thread_id}, queue size: {len(self._queue)}")

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

        print(f"Memory update timer set for {config.debounce_seconds}s")

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

        print(f"Processing {len(contexts_to_process)} queued memory updates")

        try:
            updater = MemoryUpdater()

            for context in contexts_to_process:
                try:
                    print(f"Updating memory for thread {context.thread_id}")
                    success = updater.update_memory(
                        messages=context.messages,
                        thread_id=context.thread_id,
                        agent_name=context.agent_name,
                    )
                    if success:
                        print(f"Memory updated successfully for thread {context.thread_id}")
                    else:
                        print(f"Memory update skipped/failed for thread {context.thread_id}")
                except Exception as e:
                    print(f"Error updating memory for thread {context.thread_id}: {e}")

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
