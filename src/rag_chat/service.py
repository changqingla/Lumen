"""
RAG 服务层
"""
import logging
import json
from typing import AsyncGenerator, List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from .agent_client import agent_client
from .schemas import ChatRequest, StreamChunk
from repositories.kb_repository import KnowledgeBaseRepository
from repositories.document_repository import DocumentRepository
from utils.token_usage_queue import get_producer
from utils.es_utils import get_user_es_index
from models.user import User
from services.quota_service import QuotaService
from exceptions import QuotaExceeded

logger = logging.getLogger(__name__)


class RAGService:
    """RAG 服务"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.kb_repo = KnowledgeBaseRepository(db)
        self.doc_repo = DocumentRepository(db)
    
    async def _get_es_index_names(self, user_id: UUID, kb_id: Optional[str] = None) -> List[str]:
        """
        获取 ES 索引名称
        
        注意：每个用户一个索引，所有知识库的文档都在同一个索引中
        对于共享知识库，需要使用知识库所有者的索引
        
        Args:
            user_id: 当前用户ID
            kb_id: 知识库ID（可选）
            
        Returns:
            List[str]: ES 索引名称列表（实际只有一个）
        """
        # 如果指定了知识库，使用知识库所有者的索引
        if kb_id:
            kb = await self.kb_repo.get_by_id_any(kb_id)
            if kb:
                owner_id = str(kb.owner_id)
                owner_index = get_user_es_index(owner_id)
                return [owner_index]
        
        # 默认使用当前用户的索引
        user_index = get_user_es_index(str(user_id))
        return [user_index]
    
    async def _get_doc_ids(
        self, 
        kb_id: Optional[str] = None, 
        doc_ids: Optional[List[str]] = None
    ) -> Optional[List[str]]:
        """
        获取文档ID列表
        
        Args:
            kb_id: 知识库ID（可选）
            doc_ids: 明确指定的文档ID列表（可选）
            
        Returns:
            Optional[List[str]]: 文档ID列表，None 表示不限制
        """
        if doc_ids:
            # 如果明确指定了文档ID，直接返回
            return doc_ids
        
        if kb_id:
            # 如果指定了知识库，返回该知识库下的所有文档ID
            try:
                # 直接使用get_all_doc_ids方法（返回字符串ID列表）
                doc_ids_list = await self.doc_repo.get_all_doc_ids(kb_id)
                logger.info(f"Found {len(doc_ids_list)} documents in kb {kb_id}")
                return doc_ids_list if doc_ids_list else None
            except Exception as e:
                logger.error(f"Failed to get doc IDs for kb {kb_id}: {e}", exc_info=True)
                return None
        
        # 不限制文档范围
        return None
    
    async def _get_single_document_content(
        self,
        doc_id: str,
        kb_id: str,
        user_id: UUID
    ) -> Optional[str]:
        """
        获取单个文档的markdown内容（用于直接内容模式）
        
        Args:
            doc_id: 文档ID
            kb_id: 知识库ID
            user_id: 用户ID
            
        Returns:
            Optional[str]: 文档的markdown内容，失败返回None
        """
        try:
            from services.document_service import DocumentService
            
            doc_service = DocumentService(self.db)
            markdown_content = await doc_service.get_document_markdown(
                doc_id=doc_id,
                kb_id=kb_id,
                user_id=str(user_id)
            )
            
            if markdown_content:
                logger.info(f"Successfully loaded markdown content for doc {doc_id} (length: {len(markdown_content)})")
                return markdown_content
            else:
                logger.warning(f"Markdown content is empty for doc {doc_id}")
                return None
                
        except Exception as e:
            logger.warning(f"Failed to get markdown content for doc {doc_id}: {e}")
            return None
    
    async def _get_multiple_documents_content(
        self,
        doc_ids: List[str],
        kb_id: str,
        user_id: UUID
    ) -> dict:
        """
        批量获取多个文档的markdown内容（用于多文档总结模式）
        
        Args:
            doc_ids: 文档ID列表
            kb_id: 知识库ID
            user_id: 用户ID
            
        Returns:
            {
                "documents": Dict[doc_id, markdown_content],
                "document_names": Dict[doc_id, doc_name],  # 🔑 文档名称映射
                "failed": List[doc_id]
            }
        """
        try:
            from services.document_service import DocumentService
            
            doc_service = DocumentService(self.db)
            result = await doc_service.get_documents_markdown_batch(
                doc_ids=doc_ids,
                kb_id=kb_id,
                user_id=str(user_id)
            )
            
            documents = result.get("documents", {})
            document_names = result.get("document_names", {})  # 🔑 获取文档名称映射
            failed = result.get("failed", [])
            
            if failed:
                logger.warning(f"Failed to load {len(failed)} documents: {failed}")
            
            logger.info(f"Successfully loaded markdown for {len(documents)}/{len(doc_ids)} documents")
            logger.info(f"Collected {len(document_names)} document names")
            
            # 🔑 返回完整结果，包括文档名称
            return {
                "documents": documents,
                "document_names": document_names,
                "failed": failed
            }
                
        except Exception as e:
            logger.error(f"Failed to batch load markdown content: {e}")
            return {"documents": {}, "document_names": {}, "failed": []}
    
    
    async def chat_stream(
        self,
        request: ChatRequest,
        user: User
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        流式聊天（使用Agent System）
        
        Args:
            request: 前端聊天请求
            user: 用户对象（包含会员等级信息）
            
        Yields:
            StreamChunk: 流式响应块
        """
        user_id = user.id
        is_member = user.is_member()
        
        logger.info(f"Chat stream: user={user.email}, is_member={is_member}")
        
        # 配额检查
        quota_service = QuotaService(self.db)
        quota_status = await quota_service.check_quota(user)
        
        if quota_status.is_exceeded:
            raise QuotaExceeded(
                message=quota_status.exceeded_message,
                used_tokens=quota_status.used_tokens,
                quota_limit=quota_status.quota_limit,
                reset_date=quota_status.billing_cycle_end.isoformat(),
                user_level=quota_status.user_level
            )
        
        try:
            index_names = await self._get_es_index_names(user_id, request.kb_id)
            
            doc_ids = None
            content = None
            document_contents = None
            document_names = None
            
            if request.mode == 'search':
                logger.info(f"Web search mode: session={request.session_id}")
            else:
                doc_ids = await self._get_doc_ids(request.kb_id, request.doc_ids)
                
                logger.info(f"KB mode: session={request.session_id}, kb={request.kb_id}, docs={len(doc_ids) if doc_ids else 0}")
                
                if doc_ids and len(doc_ids) == 1 and request.kb_id:
                    content = await self._get_single_document_content(
                        doc_id=doc_ids[0],
                        kb_id=request.kb_id,
                        user_id=user_id
                    )
                elif doc_ids and len(doc_ids) > 1 and request.kb_id:
                    batch_result = await self._get_multiple_documents_content(
                        doc_ids=doc_ids,
                        kb_id=request.kb_id,
                        user_id=user_id
                    )
                    document_contents = batch_result.get("documents", {}) if isinstance(batch_result, dict) else batch_result
                    document_names = batch_result.get("document_names", {}) if isinstance(batch_result, dict) else {}
            
            async for chunk in agent_client.stream_chat_completion(
                user_query=request.message,
                session_id=request.session_id,
                mode=request.mode,
                index_names=index_names,
                doc_ids=doc_ids,
                content=content,
                document_contents=document_contents,
                document_names=document_names,
                kb_id=request.kb_id,
                user_id=str(user_id),
                enable_web_search=request.enable_web_search,
                show_thinking=request.show_thinking,
                is_member=is_member
            ):
                # Handle token_usage event - push to async queue (non-blocking)
                if chunk.type == "token_usage":
                    try:
                        usage_data = json.loads(chunk.content)
                        producer = get_producer()
                        if producer:
                            await producer.push(
                                user_id=str(user_id),
                                model_name=usage_data.get("model_name", "unknown"),
                                input_tokens=usage_data.get("input_tokens", 0),
                                output_tokens=usage_data.get("output_tokens", 0),
                                session_id=usage_data.get("session_id"),
                                request_type=usage_data.get("request_type")
                            )
                            logger.debug(
                                f"Queued token usage: user={user_id}, "
                                f"input={usage_data.get('input_tokens')}, "
                                f"output={usage_data.get('output_tokens')}"
                            )
                        else:
                            logger.warning("Token usage queue producer not initialized")
                    except Exception as e:
                        logger.error(f"Failed to queue token usage: {e}")
                    # Don't yield token_usage to client
                    continue
                
                yield chunk
        
        except Exception as e:
            logger.error(f"Error in chat_stream: {e}", exc_info=True)
            yield StreamChunk(
                type="error",
                content=f"Chat error: {str(e)}"
            )
