"""自动生成线程标题的中间件。"""

from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from src.config.title_config import get_title_config
from src.models import create_chat_model


class TitleMiddlewareState(AgentState):
    """与 `ThreadState` 模式兼容。"""

    title: NotRequired[str | None]


class TitleMiddleware(AgentMiddleware[TitleMiddlewareState]):
    """在首轮用户交互后自动生成线程标题。"""

    state_schema = TitleMiddlewareState

    def _should_generate_title(self, state: TitleMiddlewareState) -> bool:
        """判断当前线程是否需要生成标题。"""
        config = get_title_config()
        if not config.enabled:
            return False

        # 若状态中已有标题则无需生成
        if state.get("title"):
            return False

        # 检查是否为首轮完整交互（至少含 1 条用户消息和 1 条 assistant 回复）
        messages = state.get("messages", [])
        if len(messages) < 2:
            return False

        # 统计用户消息与 assistant 消息数量
        user_messages = [m for m in messages if m.type == "human"]
        assistant_messages = [m for m in messages if m.type == "ai"]

        # 在首轮完整往返后生成标题
        return len(user_messages) == 1 and len(assistant_messages) >= 1

    async def _generate_title(self, state: TitleMiddlewareState) -> str:
        """根据对话内容生成简洁标题。"""
        config = get_title_config()
        messages = state.get("messages", [])

        # 获取首条用户消息与首条 assistant 回复
        user_msg_content = next((m.content for m in messages if m.type == "human"), "")
        assistant_msg_content = next((m.content for m in messages if m.type == "ai"), "")

        # 确保内容为字符串（LangChain 消息内容可能为列表）
        user_msg = str(user_msg_content) if user_msg_content else ""
        assistant_msg = str(assistant_msg_content) if assistant_msg_content else ""

        # 使用轻量模型生成标题
        model = create_chat_model(thinking_enabled=False)

        prompt = config.prompt_template.format(
            max_words=config.max_words,
            user_msg=user_msg[:500],
            assistant_msg=assistant_msg[:500],
        )

        try:
            response = await model.ainvoke(prompt)
            # 确保响应内容为字符串
            title_content = str(response.content) if response.content else ""
            title = title_content.strip().strip('"').strip("'")
            # 限制最大字符长度
            return title[: config.max_chars] if len(title) > config.max_chars else title
        except Exception as e:
            print(f"Failed to generate title: {e}")
            # 回退方案：截取用户消息前缀（按字符数）
            fallback_chars = min(config.max_chars, 50)  # 取 max_chars 与 50 的较小值
            if len(user_msg) > fallback_chars:
                return user_msg[:fallback_chars].rstrip() + "..."
            return user_msg if user_msg else "New Conversation"

    @override
    async def aafter_model(self, state: TitleMiddlewareState, runtime: Runtime) -> dict | None:
        """在首轮 agent 响应后生成并写入线程标题。"""
        if self._should_generate_title(state):
            title = await self._generate_title(state)
            print(f"Generated thread title: {title}")

            # 将标题写入状态（若配置了 checkpointer 将被持久化）
            return {"title": title}

        return None
