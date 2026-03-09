"""
Agent System客户端适配器
调用本地Agent System的/query/stream接口
"""
import httpx
import logging
import json
from typing import AsyncGenerator, List, Optional
from .config import rag_settings
from .schemas import StreamChunk

logger = logging.getLogger(__name__)


class AgentClient:
    """Agent System客户端"""
    
    def __init__(self):
        self.base_url = rag_settings.AGENT_SYSTEM_URL
    
    async def stream_chat_completion(
        self,
        user_query: str,
        session_id: str,
        mode: str,
        index_names: List[str],
        doc_ids: Optional[List[str]] = None,
        content: Optional[str] = None,
        document_contents: Optional[dict] = None,
        document_names: Optional[dict] = None,
        kb_id: Optional[str] = None,
        user_id: Optional[str] = None,
        enable_web_search: bool = False,
        show_thinking: bool = True,
        is_member: bool = False
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        调用Agent System的流式对话接口
        """
        url = f"{self.base_url}/query/stream"
        
        try:
            model_name, model_url, api_key, max_context_tokens = rag_settings.get_llm_config(is_member)
            
            logger.debug(f"Agent request: session={session_id}, model={model_name}")
            
            agent_request = {
                "user_query": user_query,
                "session_id": session_id,
                "enable_web_search": enable_web_search,
                "show_thinking": show_thinking,
                "content": content,
                "document_contents": document_contents,
                "document_names": document_names,
                "kb_id": kb_id,
                "user_id": user_id,
                "openai_api_key": api_key,
                "openai_api_base": model_url,
                "model_name": model_name,
                "max_context_tokens": max_context_tokens,
                "search_engine": rag_settings.SEARCH_ENGINE,
                "search_engine_api_key": rag_settings.SEARCH_ENGINE_API_KEY if enable_web_search else None,
                "recall_index_names": ",".join(index_names),
                "recall_doc_ids": ",".join(doc_ids) if doc_ids else "",
                "recall_api_url": rag_settings.RECALL_API_URL,
                "recall_es_host": rag_settings.ES_HOST,
                "recall_top_n": rag_settings.RECALL_TOP_N,
                "recall_similarity_threshold": rag_settings.RECALL_SIMILARITY_THRESHOLD,
                "recall_vector_similarity_weight": rag_settings.RECALL_VECTOR_SIMILARITY_WEIGHT,
                "recall_model_factory": rag_settings.EMBEDDING_MODEL_FACTORY,
                "recall_model_name": rag_settings.EMBEDDING_MODEL_NAME,
                "recall_model_base_url": rag_settings.EMBEDDING_BASE_URL,
                "recall_api_key": rag_settings.EMBEDDING_API_KEY,
                "recall_use_rerank": rag_settings.RECALL_USE_RERANK,
                "recall_rerank_factory": rag_settings.RERANK_FACTORY,
                "recall_rerank_model_name": rag_settings.RERANK_MODEL_NAME,
                "recall_rerank_base_url": rag_settings.RERANK_BASE_URL,
                "recall_rerank_api_key": rag_settings.RERANK_API_KEY,
            }
            
            async with httpx.AsyncClient(timeout=3000.0) as client:
                async with client.stream("POST", url, json=agent_request) as response:
                    response.raise_for_status()
                    
                    current_event = None
                    
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                        elif line.startswith("data: "):
                            data = line[6:].strip()
                            try:
                                event_data = json.loads(data)
                                chunk = self._convert_event(current_event, event_data)
                                if chunk:
                                    yield chunk
                            except json.JSONDecodeError as e:
                                logger.warning(f"Failed to parse data: {e}")
                                continue
        
        except httpx.HTTPStatusError as e:
            logger.error(f"Agent System HTTP error: {e}")
            yield StreamChunk(type="error", content=f"Agent System error: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Agent System request error: {e}")
            yield StreamChunk(type="error", content=f"Failed to connect to Agent System: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            yield StreamChunk(type="error", content=f"Unexpected error: {str(e)}")
    
    def _convert_event(self, event_type: str, event_data: dict) -> Optional[StreamChunk]:
        """转换Agent System事件为StreamChunk格式"""
        if not event_type:
            return None

        if event_type == "answer_chunk":
            return StreamChunk(type="token", content=event_data.get("content", ""))
        elif event_type == "thought_chunk":
            return StreamChunk(type="thinking", content=event_data.get("content", ""))
        elif event_type in ["thinking_start", "thinking_end"]:
            return None
        elif event_type == "final_answer":
            return StreamChunk(
                type="final_answer",
                content=json.dumps({
                    "answer": event_data.get("answer", ""),
                    "session_id": event_data.get("session_id", ""),
                })
            )
        elif event_type == "error":
            return StreamChunk(type="error", content=event_data.get("message", "Unknown error"))
        elif event_type == "cancelled":
            return StreamChunk(type="cancelled", content=json.dumps(event_data))
        elif event_type == "token_usage":
            # Forward token_usage event for service layer to handle
            return StreamChunk(type="token_usage", content=json.dumps(event_data))
        else:
            return None
    
    async def cancel_generation(self, session_id: str) -> dict:
        """
        Cancel an ongoing generation for a session.
        
        Args:
            session_id: Session ID to cancel
            
        Returns:
            Response from Agent System
        """
        url = f"{self.base_url}/cancel/{session_id}"
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Agent System cancel HTTP error: {e}")
            return {"success": False, "error": f"HTTP error: {e.response.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"Agent System cancel request error: {e}")
            return {"success": False, "error": f"Request error: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error during cancel: {e}", exc_info=True)
            return {"success": False, "error": f"Unexpected error: {str(e)}"}


agent_client = AgentClient()

