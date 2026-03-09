"""State definitions for the agent system — atomic tools architecture."""
from typing import Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """Agent 状态 — 仅保留 ReAct 循环所需字段。"""

    # 用户输入
    user_query: str
    enable_web_search: bool

    # 文档信息
    document_ids: Optional[List[str]]
    document_names: Optional[Dict[str, str]]
    document_contents: Optional[Dict[str, str]]
    direct_content: Optional[str]
    content_token_count: Optional[int]

    # ReAct 状态
    react_iteration: Optional[int]
    final_answer: str
    messages: List[BaseMessage]

    # 会话管理
    session_id: Optional[str]
    session_history: Optional[List]
    session_tokens: Optional[int]
    _user_message_saved: Optional[bool]

    # 资源配置
    max_context_tokens: Optional[int]
    token_counter: Optional[Any]  # TokenCounter

    # 元数据
    start_time: Optional[float]
    error: Optional[str]

    # 工作目录管理
    workspace_path: Optional[str]
    request_id: Optional[str]
    workspace_created_at: Optional[float]
    active_skill_runtime: Optional[dict]
