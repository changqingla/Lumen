"""Tenant-scoped long-term memory entry points.

该模块提供以下能力：
- 按 Backend 签发的 opaque scope 存储用户上下文与会话历史
- 使用 LLM 对对话进行摘要与事实提取
- 将相关记忆注入系统提示词以实现个性化回复

旧的全局 `memory.json` 不会被读取或自动迁移。
"""

from src.agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
    format_memory_for_injection,
)
from src.agents.memory.queue import (
    ConversationContext,
    MemoryUpdateQueue,
    get_memory_queue,
)
from src.agents.memory.updater import (
    MemoryUpdater,
    get_memory_data,
    reload_memory_data,
)

__all__ = [
    # 提示词工具
    "MEMORY_UPDATE_PROMPT",
    "format_memory_for_injection",
    "format_conversation_for_update",
    # 队列
    "ConversationContext",
    "MemoryUpdateQueue",
    "get_memory_queue",
    # 更新器
    "MemoryUpdater",
    "get_memory_data",
    "reload_memory_data",
]
