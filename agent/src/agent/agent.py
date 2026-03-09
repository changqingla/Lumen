"""Main agent class — atomic tools architecture."""
import asyncio
import json
import time
import uuid
from typing import Dict, Any, Optional, List

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from .state import AgentState
from .nodes import AgentNodes
from ..tools import create_recall_tool, create_web_search_tool
from ..utils.logger import get_logger
from ..utils.token_counter import TokenCounter
from ..utils.workspace_manager import get_workspace_manager
from config import get_settings

from context.session_manager import SessionManager
from context.session_storage import SessionStorage

logger = get_logger(__name__)


def normalize_doc_id(doc_id: str) -> str:
    return doc_id.strip()


def parse_and_normalize_doc_ids(doc_ids_str: str) -> List[str]:
    if not doc_ids_str or not doc_ids_str.strip():
        return []
    if doc_ids_str.strip().startswith("["):
        try:
            doc_ids = json.loads(doc_ids_str)
            if not isinstance(doc_ids, list):
                raise ValueError(f"Expected JSON array, got {type(doc_ids)}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON array format for doc_ids: {str(e)}")
    else:
        doc_ids = [d.strip() for d in doc_ids_str.split(",")]
    return [normalize_doc_id(d) for d in doc_ids if d and d.strip()]


class IntelligentAgent:
    """Main intelligent agent — atomic tools architecture."""

    def __init__(self):
        self.settings = get_settings()
        logger.info("Initializing IntelligentAgent (atomic tools mode)...")
        storage = SessionStorage()
        self.session_manager = SessionManager(storage)
        logger.info("✅ IntelligentAgent initialization complete")

    def get_conversation_history(self, session_id: str) -> List[Dict[str, Any]]:
        messages = self.session_manager.get_conversation_history(session_id)
        if not messages:
            return []
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "type": msg.message_type.value,
                "token_count": msg.token_count,
                "created_at": msg.created_at.isoformat(),
                "is_compressed": msg.is_compressed,
            }
            for msg in messages
        ]

    def clear_conversation(self, session_id: str) -> bool:
        logger.info(f"清除会话请求: {session_id}")
        return True

    async def process_query_stream(self, show_thinking: bool = True, **kwargs):
        """统一 ReAct Agent 流式处理 — 原子工具架构。"""
        start_time = time.time()
        token_counter = TokenCounter("openai")

        # ── 参数提取 ─────────────────────────────────────────
        user_query = kwargs.get("user_query")
        enable_web_search = kwargs.get("enable_web_search")
        session_id = kwargs.get("session_id")
        content = kwargs.get("content")
        document_contents = kwargs.get("document_contents")
        document_names = kwargs.get("document_names")

        openai_api_key = kwargs.get("openai_api_key")
        openai_api_base = kwargs.get("openai_api_base")
        model_name = kwargs.get("model_name")
        max_context_tokens = kwargs.get("max_context_tokens")
        search_engine = kwargs.get("search_engine", "tavily")
        search_engine_api_key = kwargs.get("search_engine_api_key")

        recall_api_url = kwargs.get("recall_api_url")
        recall_index_names = kwargs.get("recall_index_names")
        recall_doc_ids = kwargs.get("recall_doc_ids", "")
        recall_es_host = kwargs.get("recall_es_host")
        recall_top_n = kwargs.get("recall_top_n")
        recall_similarity_threshold = kwargs.get("recall_similarity_threshold")
        recall_vector_similarity_weight = kwargs.get("recall_vector_similarity_weight")
        recall_model_factory = kwargs.get("recall_model_factory")
        recall_model_name = kwargs.get("recall_model_name")
        recall_model_base_url = kwargs.get("recall_model_base_url")
        recall_api_key = kwargs.get("recall_api_key")
        recall_use_rerank = kwargs.get("recall_use_rerank")
        recall_rerank_factory = kwargs.get("recall_rerank_factory", "")
        recall_rerank_model_name = kwargs.get("recall_rerank_model_name", "")
        recall_rerank_base_url = kwargs.get("recall_rerank_base_url", "")
        recall_rerank_api_key = kwargs.get("recall_rerank_api_key", "")

        # ── 参数验证 ─────────────────────────────────────────
        required_params = {
            "openai_api_key": openai_api_key,
            "openai_api_base": openai_api_base,
            "model_name": model_name,
            "max_context_tokens": max_context_tokens,
            "recall_api_url": recall_api_url,
            "recall_index_names": recall_index_names,
            "recall_es_host": recall_es_host,
            "recall_top_n": recall_top_n,
            "recall_similarity_threshold": recall_similarity_threshold,
            "recall_vector_similarity_weight": recall_vector_similarity_weight,
            "recall_model_factory": recall_model_factory,
            "recall_model_name": recall_model_name,
            "recall_model_base_url": recall_model_base_url,
            "recall_api_key": recall_api_key,
            "recall_use_rerank": recall_use_rerank,
        }
        missing = [k for k, v in required_params.items() if v is None]
        if missing:
            raise ValueError(f"Missing required parameters: {', '.join(missing)}")

        if recall_use_rerank:
            rr = {
                "recall_rerank_factory": recall_rerank_factory,
                "recall_rerank_model_name": recall_rerank_model_name,
                "recall_rerank_base_url": recall_rerank_base_url,
                "recall_rerank_api_key": recall_rerank_api_key,
            }
            missing_rr = [k for k, v in rr.items() if not v]
            if missing_rr:
                raise ValueError(f"Rerank enabled but missing: {', '.join(missing_rr)}")

        # ── 资源创建 ─────────────────────────────────────────
        runtime_llm = ChatOpenAI(
            model=model_name, temperature=self.settings.temperature,
            openai_api_key=openai_api_key, openai_api_base=openai_api_base,
            streaming=True, stream_usage=True,
            model_kwargs={"stream_options": {"include_usage": True}},
        )

        document_ids = parse_and_normalize_doc_ids(recall_doc_ids)

        def _parse_index(s: str) -> list:
            return [n.strip() for n in s.split(",")] if s else []

        recall_tool_instance = create_recall_tool(
            api_url=recall_api_url, index_names=_parse_index(recall_index_names),
            es_host=recall_es_host, model_base_url=recall_model_base_url,
            api_key=recall_api_key, doc_ids=document_ids, top_n=recall_top_n,
            similarity_threshold=recall_similarity_threshold,
            vector_similarity_weight=recall_vector_similarity_weight,
            model_factory=recall_model_factory, model_name=recall_model_name,
            use_rerank=recall_use_rerank,
            rerank_factory=recall_rerank_factory if recall_use_rerank and recall_rerank_factory else None,
            rerank_model_name=recall_rerank_model_name if recall_use_rerank and recall_rerank_model_name else None,
            rerank_base_url=recall_rerank_base_url if recall_use_rerank and recall_rerank_base_url else None,
            rerank_api_key=recall_rerank_api_key if recall_use_rerank and recall_rerank_api_key else None,
        )

        final_web = enable_web_search if enable_web_search is not None else self.settings.enable_web_search
        web_tool = None
        if final_web and search_engine_api_key:
            web_tool = create_web_search_tool(
                api_key=search_engine_api_key, search_engine=search_engine,
                max_results=self.settings.search_max_results,
            )

        if session_id is None:
            session_id = str(uuid.uuid4())
        logger.info(f"Processing query [session: {session_id}]: {user_query[:100]}...")

        # ── Workspace 生命周期管理 ────────────────────────────
        workspace_manager = get_workspace_manager()
        request_id = str(uuid.uuid4())
        workspace_path = workspace_manager.create(session_id, request_id)

        # ── 会话管理 ─────────────────────────────────────────
        session = self.session_manager.get_or_create_session(session_id=session_id)
        session_id = str(session.session_id)
        session_tokens = session.total_token_count
        session_msgs = self.session_manager.get_conversation_history(session_id)
        session_history = session_msgs if session_msgs else None

        # 直接内容模式判断
        direct_content_value = None
        if content and len(document_ids) <= 1:
            tk = TokenCounter.estimate_tokens(content)
            avail = max(0, max_context_tokens - session_tokens - TokenCounter.estimate_tokens(user_query))
            if tk <= int(avail * self.settings.direct_content_threshold):
                direct_content_value = content

        # ── AgentNodes ───────────────────────────────────────
        agent_nodes = AgentNodes(
            llm=runtime_llm, recall_tool=recall_tool_instance,
            session_manager=self.session_manager, web_search_tool=web_tool,
        )
        # 注入文档数据到阅读工具
        agent_nodes.set_document_context(document_contents, document_names)
        # 注入 Workspace 上下文到文件工具
        agent_nodes.set_workspace_context(str(workspace_path), session_id)

        # ── 状态初始化 ───────────────────────────────────────
        state: AgentState = {
            "user_query": user_query,
            "enable_web_search": final_web,
            "document_ids": document_ids,
            "document_names": document_names,
            "document_contents": document_contents,
            "direct_content": direct_content_value,
            "content_token_count": TokenCounter.estimate_tokens(content) if content else None,
            "final_answer": "",
            "messages": [HumanMessage(content=user_query)],
            "session_id": session_id,
            "session_history": session_history,
            "session_tokens": session_tokens,
            "_user_message_saved": False,
            "max_context_tokens": max_context_tokens,
            "token_counter": token_counter,
            "start_time": start_time,
            "error": None,
            "react_iteration": None,
            "workspace_path": str(workspace_path),
            "request_id": request_id,
            "workspace_created_at": time.time(),
            "active_skill_runtime": None,
        }

        # ── ReAct 循环 ──────────────────────────────────────
        if show_thinking:
            yield {"type": "thinking_start", "data": {"content": "<think>\n"}}
            yield {"type": "thinking_end", "data": {"content": "</think>\n\n"}}

        try:
            try:
                async for event in agent_nodes.react_agent_node_stream(state):
                    etype = event["type"]
                    if etype == "thought_chunk":
                        yield {"type": "thought_chunk", "data": {"content": event["data"]["content"]}}
                    elif etype == "answer_chunk":
                        yield {"type": "answer_chunk", "data": {"content": event["data"]["content"]}}
                    elif etype == "node_complete":
                        state.update(event["data"])
                    elif etype == "node_error":
                        logger.error(f"Node error: {event.get('error')}")
                        yield {"type": "error", "data": {"message": f"ReAct agent failed: {event.get('error')}"}}
                        return

                # ── 输出结果 ────────────────────────────────────
                yield {
                    "type": "final_answer",
                    "data": {"answer": state["final_answer"], "session_id": session_id},
                }

                inp = token_counter.input_tokens if token_counter.total_tokens > 0 else 0
                out = token_counter.output_tokens if token_counter.total_tokens > 0 else 0
                yield {
                    "type": "token_usage",
                    "data": {
                        "input_tokens": inp, "output_tokens": out,
                        "total_tokens": token_counter.total_tokens,
                        "model_name": model_name, "session_id": session_id,
                        "user_id": kwargs.get("user_id"), "request_type": "react",
                    },
                }

            except Exception as e:
                logger.error(f"Error in streaming query: {str(e)}", exc_info=True)
                try:
                    yield {
                        "type": "token_usage",
                        "data": {
                            "input_tokens": token_counter.input_tokens,
                            "output_tokens": token_counter.output_tokens,
                            "total_tokens": token_counter.total_tokens,
                            "model_name": model_name, "session_id": session_id,
                            "user_id": kwargs.get("user_id"), "request_type": "error",
                        },
                    }
                except Exception:
                    pass
                yield {"type": "error", "data": {"message": str(e), "session_id": session_id}}
            finally:
                # 清理 recall_tool 的 ES 连接，防止连接泄漏
                try:
                    if recall_tool_instance and recall_tool_instance._retriever:
                        await recall_tool_instance._retriever.close()
                        logger.debug("Recall tool ES connection closed")
                except Exception as e:
                    logger.warning(f"Failed to close recall tool ES connection: {e}")
        finally:
            # 清理 Workspace 目录
            workspace_manager.cleanup(str(workspace_path))


def create_agent() -> IntelligentAgent:
    return IntelligentAgent()
