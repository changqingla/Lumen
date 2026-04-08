"""
聊天会话服务层
"""
from typing import List, Optional
from uuid import UUID

from modules.chat.repositories.chat_repository import ChatRepository
from modules.chat.entities.chat_session import ChatSession, ChatMessage


class ChatService:
    """聊天会话服务"""
    
    def __init__(self, chat_repo: ChatRepository):
        self.chat_repo = chat_repo
    
    async def create_or_get_session(
        self, 
        user_id: UUID, 
        first_message: str,
        config: Optional[dict] = None
    ) -> ChatSession:
        """创建或获取聊天会话"""
        # 生成标题（取前30个字符）
        title = first_message[:30] + ("..." if len(first_message) > 30 else "")
        session = await self.chat_repo.create_session(user_id, title, config=config)
        return session

    async def create_empty_session(
        self,
        user_id: UUID,
        config: Optional[dict] = None,
        title: str = "新对话",
    ) -> ChatSession:
        """创建空聊天会话，供会话级附件上传等场景使用。"""
        resolved_title = (title or "").strip() or "新对话"
        session = await self.chat_repo.create_session(user_id, resolved_title, config=config)
        return session
    
    async def get_session(self, session_id: UUID, user_id: UUID) -> Optional[ChatSession]:
        """获取会话（验证所有权）"""
        session = await self.chat_repo.get_session(session_id)
        if session and session.user_id == user_id:
            return session
        return None
    
    async def list_sessions(self, user_id: UUID, page: int = 1, page_size: int = 50) -> List[ChatSession]:
        """获取用户的所有会话"""
        return await self.chat_repo.list_user_sessions(user_id, page, page_size)
    
    async def delete_session(self, session_id: UUID, user_id: UUID) -> bool:
        """删除会话（验证所有权）"""
        session = await self.get_session(session_id, user_id)
        if not session:
            return False
        return await self.chat_repo.delete_session(session_id)

    async def delete_all_sessions(self, user_id: UUID) -> int:
        """删除用户的所有会话"""
        return await self.chat_repo.delete_all_user_sessions(user_id)

    async def update_session_config(self, session_id: UUID, user_id: UUID, config_updates: dict) -> Optional[ChatSession]:
        """更新会话配置（验证所有权）"""
        # 先验证所有权
        session = await self.get_session(session_id, user_id)
        if not session:
            return None

        # 更新配置
        return await self.chat_repo.update_session_config(session_id, config_updates)
    
    async def add_message(
        self,
        session_id: UUID,
        user_id: UUID,
        role: str,
        content: str,
        message_id: Optional[UUID] = None,
        thinking: Optional[str] = None,
        document_summaries: Optional[list] = None,
        image_data_urls: Optional[list[str]] = None,
        artifacts: Optional[list[dict]] = None,
        attachments: Optional[list[dict]] = None,
        tool_traces: Optional[list[dict]] = None,
        assistant_tuple_messages: Optional[list[dict]] = None,
        truncation_metadata: Optional[dict] = None,
        interruption: Optional[dict] = None,
    ) -> Optional[ChatMessage]:
        """添加消息（原子性验证所有权）"""
        # ✅ 使用原子性操作，在 Repository 层面同时验证所有权和添加消息
        return await self.chat_repo.add_message_with_ownership_check(
            session_id,
            user_id,
            role,
            content,
            message_id,
            thinking,
            document_summaries,
            image_data_urls,
            artifacts,
            attachments,
            tool_traces,
            assistant_tuple_messages,
            truncation_metadata,
            interruption,
        )
    
    async def get_messages(self, session_id: UUID, user_id: UUID) -> List[ChatMessage]:
        """获取会话消息（验证所有权）"""
        session = await self.get_session(session_id, user_id)
        if not session:
            return []
        
        return await self.chat_repo.get_session_messages(session_id)

    async def delete_last_assistant_message(
        self,
        session_id: UUID,
        user_id: UUID
    ) -> Optional[str]:
        """
        删除最后一条 AI 回复（验证所有权）
        
        Args:
            session_id: 会话ID
            user_id: 用户ID
            
        Returns:
            被删除的消息ID，如果没有找到则返回 None
        """
        return await self.chat_repo.delete_last_assistant_message(session_id, user_id)
