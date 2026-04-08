"""防止模型在同一轮内重复执行相同工具调用的中间件。"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = logging.getLogger(__name__)


def _stable_json(value: object) -> str:
    """将工具参数稳定序列化，便于生成指纹。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr, separators=(",", ":"))


def _tool_call_signature(tool_name: str, args: object) -> str:
    """为工具调用生成稳定指纹。"""
    payload = f"{tool_name}:{_stable_json(args)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ToolCallLoopGuardMiddleware(AgentMiddleware[AgentState]):
    """拦截同一轮中重复过多次的相同工具调用，避免无限循环。"""

    def __init__(self, max_identical_calls: int = 3):
        super().__init__()
        self.max_identical_calls = max_identical_calls

    def _count_previous_identical_calls(self, request: ToolCallRequest) -> int:
        state = request.state or {}
        messages = state.get("messages", []) if isinstance(state, dict) else []
        if not messages:
            return 0

        # 仅统计当前轮（最后一条 human 消息之后）的历史调用，避免跨轮误伤。
        start_index = 0
        for idx in range(len(messages) - 1, -1, -1):
            if getattr(messages[idx], "type", None) == "human":
                start_index = idx + 1
                break

        tool_name = request.tool_call.get("name", "")
        signature = _tool_call_signature(tool_name, request.tool_call.get("args", {}))
        current_tool_call_id = request.tool_call.get("id")

        count = 0
        for msg in messages[start_index:]:
            if getattr(msg, "type", None) != "ai":
                continue
            for tool_call in getattr(msg, "tool_calls", None) or []:
                if tool_call.get("id") == current_tool_call_id:
                    continue
                if tool_call.get("name") != tool_name:
                    continue
                if _tool_call_signature(tool_name, tool_call.get("args", {})) == signature:
                    count += 1
        return count

    def _build_block_message(self, request: ToolCallRequest, previous_count: int) -> ToolMessage:
        tool_name = request.tool_call.get("name", "unknown")
        tool_call_id = request.tool_call.get("id", "")
        logger.warning(
            "Blocking repeated tool call to prevent loop: tool=%s, repeats=%s, max_identical_calls=%s",
            tool_name,
            previous_count + 1,
            self.max_identical_calls,
        )
        return ToolMessage(
            content=(
                f"Repeated identical tool call blocked to prevent an infinite loop. "
                f"The tool `{tool_name}` has already been requested {previous_count} time(s) "
                f"with the same arguments in this turn. Do not call it again unless the inputs change. "
                f"Use a different strategy, summarize what you learned, or ask the user for clarification."
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def _maybe_block(self, request: ToolCallRequest) -> ToolMessage | None:
        previous_count = self._count_previous_identical_calls(request)
        if previous_count < self.max_identical_calls:
            return None
        return self._build_block_message(request, previous_count)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return await handler(request)
