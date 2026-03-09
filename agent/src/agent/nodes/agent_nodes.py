"""AgentNodes 组合类 — 原子工具架构"""
from typing import Dict, Any, Optional, AsyncGenerator

from langchain_openai import ChatOpenAI

from .react_nodes import ReActNodes
from ..state import AgentState
from ...tools import RecallTool, WebSearchTool
from ...utils.logger import get_logger

from context.session_manager import SessionManager

logger = get_logger(__name__)


class AgentNodes:
    """Agent 节点组合类 — 统一 ReAct 架构（原子工具）"""

    def __init__(
        self,
        llm: ChatOpenAI,
        recall_tool: RecallTool,
        session_manager: SessionManager,
        web_search_tool: Optional[WebSearchTool] = None,
    ):
        self._react_nodes = ReActNodes(llm, recall_tool, session_manager, web_search_tool)
        self.llm = llm
        self.recall_tool = recall_tool
        self.session_manager = session_manager
        self.web_search_tool = web_search_tool
        logger.info("AgentNodes initialized (atomic tools)")

    def set_document_context(
        self,
        document_contents: Optional[Dict[str, str]],
        document_names: Optional[Dict[str, str]],
    ) -> None:
        """注入文档数据到阅读工具"""
        self._react_nodes.set_document_context(document_contents, document_names)

    def set_workspace_context(self, workspace_path: str, session_id: str) -> None:
        """注入工作目录上下文到所有文件工具"""
        self._react_nodes.set_workspace_context(workspace_path, session_id)

    async def react_agent_node_stream(self, state: AgentState) -> AsyncGenerator[Dict[str, Any], None]:
        async for event in self._react_nodes.react_agent_node_stream(state):
            yield event
