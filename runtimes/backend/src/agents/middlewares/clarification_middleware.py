"""用于拦截澄清请求并向用户展示的中间件。"""

from collections.abc import Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command


class ClarificationMiddlewareState(AgentState):
    """与 `ThreadState` 模式兼容。"""

    pass


class ClarificationMiddleware(AgentMiddleware[ClarificationMiddlewareState]):
    """处理模型发起的 `ask_clarification` 工具调用。

    当模型调用 `ask_clarification` 工具时，此中间件会：
    1. 在工具执行前拦截调用
    2. 提取澄清问题及相关元数据
    3. 格式化为更易读的用户消息
    4. 返回一个会中断执行并展示问题的 Command
    5. 等待用户回复后再继续流程

    该实现替代了“工具继续对话流”的旧方式。
    """

    state_schema = ClarificationMiddlewareState

    def _format_clarification_message(self, args: dict) -> str:
        """格式化澄清消息。

        参数：
            args: 包含澄清细节的工具调用参数。

        返回：
            格式化后的消息字符串。
        """
        question = args.get("question", "")
        clarification_type = args.get("clarification_type", "missing_info")
        context = args.get("context")
        options = args.get("options", [])

        # 不同澄清类型对应的图标
        type_icons = {
            "missing_info": "❓",
            "ambiguous_requirement": "🤔",
            "approach_choice": "🔀",
            "risk_confirmation": "⚠️",
            "suggestion": "💡",
        }

        icon = type_icons.get(clarification_type, "❓")

        # 以更自然的语序拼接消息
        message_parts = []

        # 将图标与问题组合，提升可读性
        if context:
            # 有上下文时先作为背景信息展示
            message_parts.append(f"{icon} {context}")
            message_parts.append(f"\n{question}")
        else:
            # 无上下文时仅展示“图标 + 问题”
            message_parts.append(f"{icon} {question}")

        # 以更清晰的列表格式展示选项
        if options and len(options) > 0:
            message_parts.append("")  # 空行用于视觉分隔
            for i, option in enumerate(options, 1):
                message_parts.append(f"  {i}. {option}")

        return "\n".join(message_parts)

    def _handle_clarification(self, request: ToolCallRequest) -> Command:
        """处理澄清请求并返回中断执行的命令。

        参数：
            request: 工具调用请求。

        返回：
            携带格式化澄清消息、可中断执行的 Command。
        """
        # 提取澄清参数
        args = request.tool_call.get("args", {})
        question = args.get("question", "")

        print("[ClarificationMiddleware] Intercepted clarification request")
        print(f"[ClarificationMiddleware] Question: {question}")

        # 生成格式化澄清消息
        formatted_message = self._format_clarification_message(args)

        # 获取工具调用 ID
        tool_call_id = request.tool_call.get("id", "")

        # 创建包含格式化问题的 ToolMessage
        # 该消息会加入会话历史
        tool_message = ToolMessage(
            content=formatted_message,
            tool_call_id=tool_call_id,
            name="ask_clarification",
        )

        # 同时追加一条真正的助手消息，让前端按正文而不是工具卡片渲染。
        assistant_message = AIMessage(content=formatted_message)

        # 返回一个 Command：
        # 1. 写入工具消息，保持工具调用闭环
        # 2. 写入助手消息，供前端按普通对话正文展示
        # 3. 跳转到 __end__ 以中断当前执行
        return Command(
            update={"messages": [tool_message, assistant_message]},
            goto=END,
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步包装工具调用。

        参数：
            request: 工具调用请求。
            handler: 原始工具执行处理器。

        返回：
            若为澄清请求则返回中断命令，否则执行原处理器。
        """
        # 判断是否为 ask_clarification 调用
        if request.tool_call.get("name") != "ask_clarification":
            # 非澄清调用，按正常流程执行
            return handler(request)

        return self._handle_clarification(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """异步包装工具调用。

        参数：
            request: 工具调用请求。
            handler: 原始工具执行处理器（异步）。

        返回：
            若为澄清请求则返回中断命令，否则执行原处理器。
        """
        # 判断是否为 ask_clarification 调用
        if request.tool_call.get("name") != "ask_clarification":
            # 非澄清调用，按正常流程执行
            return await handler(request)

        return self._handle_clarification(request)
