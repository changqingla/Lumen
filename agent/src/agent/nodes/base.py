"""Agent 节点基类和工具方法"""
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from ..state import AgentState
from ..thinking import ThoughtGeneratorManager
from ...utils.logger import get_logger
from ...utils.recall_cache import RecallToolCache, RecallResultCache
from ...tools import RecallTool, WebSearchTool
from ..constants import RECALL_TOOL_CACHE_SIZE

from context.session_manager import SessionManager
from context.context_injector import ContextInjector

logger = get_logger(__name__)


class BaseAgentNode:
    """节点基类，提供公共功能"""

    def __init__(
        self,
        llm: ChatOpenAI,
        recall_tool: RecallTool,
        session_manager: SessionManager,
        web_search_tool: Optional[WebSearchTool] = None,
    ):
        self.llm = llm
        self.recall_tool = recall_tool
        self.session_manager = session_manager
        self.web_search_tool = web_search_tool
        self.context_injector = ContextInjector()
        self.thought_manager = ThoughtGeneratorManager()
        self._recall_cache = RecallResultCache(max_size=RECALL_TOOL_CACHE_SIZE)

    async def _stream_llm_with_usage(
        self, prompt: str, state: AgentState
    ) -> Tuple[str, Any]:
        """流式调用 LLM 并正确捕获 token usage 信息"""
        full_response = ""
        last_chunk = None
        usage_chunk = None

        async for chunk in self.llm.astream([HumanMessage(content=prompt)]):
            chunk_content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_response += chunk_content
            last_chunk = chunk
            if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                usage_chunk = chunk

        token_counter = state.get("token_counter")
        if token_counter:
            c = usage_chunk if usage_chunk else last_chunk
            if c:
                token_counter.update_from_stream_final(c)

        return full_response, usage_chunk if usage_chunk else last_chunk

    async def _get_conversation_context_async(
        self, state: AgentState, stage: str = "intent_recognition"
    ) -> str:
        """异步获取对话上下文"""
        import asyncio
        return await asyncio.to_thread(self._get_conversation_context_sync, state, stage)

    def _get_conversation_context_sync(
        self, state: AgentState, stage: str = "intent_recognition"
    ) -> str:
        session_id = state.get("session_id")
        if not session_id:
            return ""

        inject_methods = {
            "intent_recognition": self.context_injector.inject_for_intent_recognition,
            "planning": self.context_injector.inject_for_planning,
            "answer_generation": self.context_injector.inject_for_answer_generation,
            "simple_interaction": self.context_injector.inject_for_simple_interaction,
        }

        inject_method = inject_methods.get(stage)
        if not inject_method:
            return ""

        messages = inject_method(session_id)
        if not messages:
            return ""

        return self.context_injector.format_messages_for_prompt(messages)

    def _get_conversation_context(
        self, state: AgentState, stage: str = "intent_recognition"
    ) -> str:
        """同步获取对话上下文（兼容旧代码）"""
        return self._get_conversation_context_sync(state, stage)
