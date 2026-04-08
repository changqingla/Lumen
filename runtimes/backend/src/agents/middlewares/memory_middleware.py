"""记忆机制中间件。"""

import re
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from src.agents.memory.queue import get_memory_queue
from src.config.memory_config import get_memory_config


class MemoryMiddlewareState(AgentState):
    """与 `ThreadState` 模式兼容。"""

    pass


def _filter_messages_for_memory(messages: list[Any]) -> list[Any]:
    """过滤用于记忆更新的消息列表。

    会过滤掉：
    - Tool 消息（中间工具调用结果）
    - 带 `tool_calls` 的 AI 消息（中间步骤，而非最终答复）
    - UploadsMiddleware 注入到 human 消息中的 `<uploaded_files>` 区块
      （文件路径是会话级信息，不应写入长期记忆）。
      用户真实问题会保留；若该轮内容仅为上传区块（移除后为空），
      则该轮及其对应 assistant 回复都会被丢弃。

    仅保留：
    - Human 消息（已移除临时上传区块）
    - 不含 `tool_calls` 的 AI 消息（最终回复），但若其配对 human
      为纯上传消息且无真实文本，则不保留该 AI 消息。

    参数：
        messages: 全量会话消息列表。

    返回：
        仅包含用户输入与最终 assistant 回复的过滤结果。
    """
    _UPLOAD_BLOCK_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)

    filtered = []
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)

        if msg_type == "human":
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            content_str = str(content)
            if "<uploaded_files>" in content_str:
                # 去除临时上传区块，保留用户真实问题
                stripped = _UPLOAD_BLOCK_RE.sub("", content_str).strip()
                if not stripped:
                    # 去除后为空：该轮仅为上传记录，跳过并忽略其配对 assistant 回复
                    skip_next_ai = True
                    continue
                # 重新构造清理后的消息，确保用户问题可用于记忆摘要
                from copy import copy

                clean_msg = copy(msg)
                clean_msg.content = stripped
                filtered.append(clean_msg)
                skip_next_ai = False
            else:
                filtered.append(msg)
                skip_next_ai = False
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                if skip_next_ai:
                    skip_next_ai = False
                    continue
                filtered.append(msg)
        # 跳过 Tool 消息与带 tool_calls 的 AI 中间消息

    return filtered


class MemoryMiddleware(AgentMiddleware[MemoryMiddlewareState]):
    """在每轮执行后将对话提交到记忆更新队列。

    该中间件会：
    1. 在每次 agent 执行后将会话加入记忆更新队列
    2. 仅保留用户输入与最终 assistant 回复（忽略工具中间步骤）
    3. 依赖队列的防抖机制合并高频更新
    4. 通过 LLM 异步完成记忆摘要与写入
    """

    state_schema = MemoryMiddlewareState

    def __init__(self, agent_name: str | None = None):
        """初始化记忆中间件。

        参数：
            agent_name: 若提供，则按 agent 维度存储记忆；否则使用全局记忆。
        """
        super().__init__()
        self._agent_name = agent_name

    @override
    def after_agent(self, state: MemoryMiddlewareState, runtime: Runtime) -> dict | None:
        """在 agent 执行后提交记忆更新任务。

        参数：
            state: 当前 agent 状态。
            runtime: 运行时上下文。

        返回：
            始终返回 None（该中间件不直接修改状态）。
        """
        config = get_memory_config()
        if not config.enabled:
            return None

        # 从 runtime 上下文获取 thread_id
        thread_id = runtime.context.get("thread_id")
        if not thread_id:
            print("MemoryMiddleware: No thread_id in context, skipping memory update")
            return None

        # 从状态中获取消息列表
        messages = state.get("messages", [])
        if not messages:
            print("MemoryMiddleware: No messages in state, skipping memory update")
            return None

        # 过滤，仅保留用户输入与最终 assistant 回复
        filtered_messages = _filter_messages_for_memory(messages)

        # 仅在存在有效会话内容时入队
        # 至少需要 1 条用户消息与 1 条 assistant 回复
        user_messages = [m for m in filtered_messages if getattr(m, "type", None) == "human"]
        assistant_messages = [m for m in filtered_messages if getattr(m, "type", None) == "ai"]

        if not user_messages or not assistant_messages:
            return None

        # 将过滤后的会话加入记忆更新队列
        queue = get_memory_queue()
        queue.add(thread_id=thread_id, messages=filtered_messages, agent_name=self._agent_name)

        return None
