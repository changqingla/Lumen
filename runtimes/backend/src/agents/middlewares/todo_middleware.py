"""待办列表（TodoList）中间件扩展：在上下文被截断时补充待办提醒。

当消息历史被截断（例如 SummarizationMiddleware 生效）时，
原始 `write_todos` 调用及其 ToolMessage 可能滑出当前上下文窗口。
本中间件会检测该情况并注入提醒消息，确保模型仍能感知未完成的待办列表。
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.todo import PlanningState, Todo
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime


def _todos_in_messages(messages: list[Any]) -> bool:
    """若 `messages` 中任一 AIMessage 含 `write_todos` 调用则返回 True。"""
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "write_todos":
                    return True
    return False


def _reminder_in_messages(messages: list[Any]) -> bool:
    """若 `messages` 中已存在 `todo_reminder` HumanMessage 则返回 True。"""
    for msg in messages:
        if isinstance(msg, HumanMessage) and getattr(msg, "name", None) == "todo_reminder":
            return True
    return False


def _format_todos(todos: list[Todo]) -> str:
    """将 Todo 列表格式化为可读字符串。"""
    lines: list[str] = []
    for todo in todos:
        status = todo.get("status", "pending")
        content = todo.get("content", "")
        lines.append(f"- [{status}] {content}")
    return "\n".join(lines)


class TodoMiddleware(TodoListMiddleware):
    """在 `write_todos` 调用被截断后补注入待办提醒。

    当原始 `write_todos` 调用因摘要等原因被移出消息窗口时，
    模型会丢失当前待办列表感知。本中间件在 `before_model` / `abefore_model`
    中检测该缺口，并注入提醒消息以持续追踪进度。
    """

    @override
    def before_model(
        self,
        state: PlanningState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """当 `write_todos` 已离开上下文窗口时注入 todo 提醒。"""
        todos: list[Todo] = state.get("todos") or []  # type: ignore[assignment]
        if not todos:
            return None

        messages = state.get("messages") or []
        if _todos_in_messages(messages):
            # `write_todos` 仍在当前上下文中，无需处理。
            return None

        if _reminder_in_messages(messages):
            # 提醒消息已注入且尚未被截断。
            return None

        # 状态（state）中仍有 todo，但原 `write_todos` 调用已不可见；
        # 注入 HumanMessage 提醒，保持模型对待办项的感知。
        formatted = _format_todos(todos)
        reminder = HumanMessage(
            name="todo_reminder",
            content=(
                "<system_reminder>\n"
                "Your todo list from earlier is no longer visible in the current context window, "
                "but it is still active. Here is the current state:\n\n"
                f"{formatted}\n\n"
                "Continue tracking and updating this todo list as you work. "
                "Call `write_todos` whenever the status of any item changes.\n"
                "</system_reminder>"
            ),
        )
        return {"messages": [reminder]}

    @override
    async def abefore_model(
        self,
        state: PlanningState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """`before_model` 的异步版本。"""
        return self.before_model(state, runtime)
