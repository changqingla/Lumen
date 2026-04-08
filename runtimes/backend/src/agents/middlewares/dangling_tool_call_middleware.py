"""补齐“悬空工具调用”的中间件。

当 AIMessage 含有 `tool_calls`，但历史中缺少对应 ToolMessage
（例如用户中断或请求取消）时，就会出现悬空工具调用，进而因消息格式不完整
导致 LLM 报错。

本中间件会在模型调用前检测并修补此类缺口：在发起工具调用的 AIMessage 之后
立即插入带错误标记的合成 ToolMessage，保证消息顺序与格式正确。

注意：这里使用 `wrap_model_call` 而不是 `before_model`，以确保补丁插入到
正确位置（紧跟对应 AIMessage），而不是被追加到消息末尾。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """扫描并修补缺失 ToolMessage 的 tool_calls。"""

    def _build_patched_messages(self, messages: list) -> list | None:
        """构造补丁后的消息列表。

        对每条存在悬空 tool_call（无对应 ToolMessage）的 AIMessage，
        会在其后插入一条合成 ToolMessage。若无需修补则返回 None。
        """
        # 收集历史中所有已存在 ToolMessage 的 tool_call_id
        existing_tool_msg_ids: set[str] = set()
        for msg in messages:
            if isinstance(msg, ToolMessage):
                existing_tool_msg_ids.add(msg.tool_call_id)

        # 先判断是否存在需要修补的缺口
        needs_patch = False
        for msg in messages:
            if getattr(msg, "type", None) != "ai":
                continue
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_msg_ids:
                    needs_patch = True
                    break
            if needs_patch:
                break

        if not needs_patch:
            return None

        # 构建新列表：在每条悬空 AIMessage 后立即插入补丁消息
        patched: list = []
        patched_ids: set[str] = set()
        patch_count = 0
        for msg in messages:
            patched.append(msg)
            if getattr(msg, "type", None) != "ai":
                continue
            for tc in getattr(msg, "tool_calls", None) or []:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_tool_msg_ids and tc_id not in patched_ids:
                    patched.append(
                        ToolMessage(
                            content="[Tool call was interrupted and did not return a result.]",
                            tool_call_id=tc_id,
                            name=tc.get("name", "unknown"),
                            status="error",
                        )
                    )
                    patched_ids.add(tc_id)
                    patch_count += 1

        logger.warning(f"Injecting {patch_count} placeholder ToolMessage(s) for dangling tool calls")
        return patched

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        patched = self._build_patched_messages(request.messages)
        if patched is not None:
            request = request.override(messages=patched)
        return await handler(request)
