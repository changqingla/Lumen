"""限制单次模型响应中子代理工具并发调用数量的中间件。"""

import logging
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from src.subagents.executor import MAX_CONCURRENT_SUBAGENTS

logger = logging.getLogger(__name__)

# `max_concurrent_subagents` 的合法范围
MIN_SUBAGENT_LIMIT = 2
MAX_SUBAGENT_LIMIT = 4


def _clamp_subagent_limit(value: int) -> int:
    """将子代理并发上限约束在合法区间 [2, 4]。"""
    return max(MIN_SUBAGENT_LIMIT, min(MAX_SUBAGENT_LIMIT, value))


class SubagentLimitMiddleware(AgentMiddleware[AgentState]):
    """对单次响应中的并行 `task` 调用做硬限制。

    当 LLM 在一次响应中生成超过 `max_concurrent` 个并行 `task` 工具调用时，
    本中间件仅保留前 `max_concurrent` 个，其余直接丢弃。相比仅靠提示词约束更可靠。

    参数：
        max_concurrent: 允许的最大并行子代理调用数。
            默认 `MAX_CONCURRENT_SUBAGENTS`（3），并强制限制在 [2, 4]。
    """

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_SUBAGENTS):
        super().__init__()
        self.max_concurrent = _clamp_subagent_limit(max_concurrent)

    def _truncate_task_calls(self, state: AgentState) -> dict | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None

        # 统计 task 工具调用数量
        task_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name") == "task"]
        if len(task_indices) <= self.max_concurrent:
            return None

        # 构造需丢弃的索引集合（超出上限的 task 调用）
        indices_to_drop = set(task_indices[self.max_concurrent :])
        truncated_tool_calls = [tc for i, tc in enumerate(tool_calls) if i not in indices_to_drop]

        dropped_count = len(indices_to_drop)
        logger.warning(f"Truncated {dropped_count} excess task tool call(s) from model response (limit: {self.max_concurrent})")

        # 用截断后的 tool_calls 替换原 AIMessage（同 id 触发替换）
        updated_msg = last_msg.model_copy(update={"tool_calls": truncated_tool_calls})
        return {"messages": [updated_msg]}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._truncate_task_calls(state)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._truncate_task_calls(state)
