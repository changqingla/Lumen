"""
聊天会话数据访问层
"""
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, update, delete
from sqlalchemy.orm import joinedload

from modules.chat.entities.chat_session import ChatSession, ChatMessage
from modules.chat.message_metadata import build_message_metadata


class ChatRepository:
    """聊天会话仓储"""
    
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _build_session_title(content: str) -> str:
        normalized = (content or "").strip()
        if not normalized:
            return "新对话"
        return normalized[:30] + ("..." if len(normalized) > 30 else "")
    
    async def create_session(
        self, 
        user_id: UUID, 
        title: str,
        config: Optional[Dict] = None
    ) -> ChatSession:
        """创建聊天会话"""
        session = ChatSession(
            user_id=user_id,
            title=title,
            config=config or {}
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session
    
    async def get_session(self, session_id: UUID) -> Optional[ChatSession]:
        """获取聊天会话"""
        stmt = select(ChatSession).where(ChatSession.id == session_id).options(
            joinedload(ChatSession.messages)
        )
        result = await self.db.execute(stmt)
        return result.unique().scalar_one_or_none()
    
    async def list_user_sessions(
        self, 
        user_id: UUID, 
        page: int = 1, 
        page_size: int = 50
    ) -> List[ChatSession]:
        """获取用户的所有聊天会话"""
        stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(desc(ChatSession.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_user_role_messages(
        self,
        user_id: UUID,
        role: str = "user",
    ) -> int:
        """Count messages by role across all sessions belonging to a user."""
        stmt = (
            select(func.count(ChatMessage.id))
            .select_from(ChatMessage)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(
                ChatSession.user_id == user_id,
                ChatMessage.role == role,
            )
        )
        result = await self.db.execute(stmt)
        return int(result.scalar() or 0)
    
    async def update_session_title(self, session_id: UUID, title: str) -> Optional[ChatSession]:
        """更新会话标题"""
        session = await self.get_session(session_id)
        if not session:
            return None

        session.title = title
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def update_session_config(self, session_id: UUID, config_updates: Dict) -> Optional[ChatSession]:
        """更新会话配置（部分更新）"""
        # 只查询会话本身，不加载消息
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        result = await self.db.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            return None

        # 合并配置：保留原有配置，只更新传入的字段
        current_config = session.config or {}
        updated_config = {**current_config, **config_updates}

        session.config = updated_config
        await self.db.commit()
        await self.db.refresh(session)
        return session
    
    async def delete_session(self, session_id: UUID) -> bool:
        """删除聊天会话"""
        # ✅ 修复：只查询会话本身，不加载消息（性能优化）
        # 数据库的 CASCADE DELETE 会自动删除关联消息
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        result = await self.db.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            return False

        await self.db.delete(session)
        await self.db.commit()
        return True

    async def delete_all_user_sessions(self, user_id: UUID) -> int:
        """删除用户的所有聊天会话"""
        # 先统计要删除的数量
        count_stmt = select(ChatSession).where(ChatSession.user_id == user_id)
        result = await self.db.execute(count_stmt)
        sessions = result.scalars().all()
        deleted_count = len(sessions)
        
        # 删除所有会话（CASCADE 会自动删除关联消息）
        delete_stmt = delete(ChatSession).where(ChatSession.user_id == user_id)
        await self.db.execute(delete_stmt)
        await self.db.commit()
        
        return deleted_count
    
    async def add_message(
        self,
        session_id: UUID,
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
    ) -> ChatMessage:
        """添加消息到会话"""
        message_metadata = self._build_message_metadata(
            document_summaries=document_summaries,
            image_data_urls=image_data_urls,
            artifacts=artifacts,
            attachments=attachments,
            tool_traces=tool_traces,
            assistant_tuple_messages=assistant_tuple_messages,
            truncation_metadata=truncation_metadata,
            interruption=interruption,
        )
        existing_message = None
        if message_id is not None:
            stmt = select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.session_id == session_id,
            )
            result = await self.db.execute(stmt)
            existing_message = result.scalar_one_or_none()

        should_refresh_title = False
        next_session_title: Optional[str] = None
        if existing_message is None and role == "user":
            count_stmt = select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)
            count_result = await self.db.execute(count_stmt)
            existing_message_count = int(count_result.scalar() or 0)
            if existing_message_count == 0:
                next_session_title = self._build_session_title(content)
                should_refresh_title = True

        session_updated_at = datetime.utcnow()
        if existing_message is not None:
            existing_message.role = role
            existing_message.content = content
            existing_message.thinking = thinking
            existing_message.document_summaries = message_metadata
            await self.db.flush()
            message = existing_message
        else:
            message_kwargs = {
                "session_id": session_id,
                "role": role,
                "content": content,
                "thinking": thinking,
                "document_summaries": message_metadata,
            }
            if message_id is not None:
                message_kwargs["id"] = message_id
            message = ChatMessage(**message_kwargs)
            self.db.add(message)
            await self.db.flush()  # ✅ 确保 message.created_at 已生成
            session_updated_at = message.created_at

        # ✅ 修复：使用 update 语句直接更新，避免加载所有消息（性能优化）
        update_stmt = (
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(
                updated_at=session_updated_at,
                **({"title": next_session_title} if should_refresh_title and next_session_title else {}),
            )
        )
        await self.db.execute(update_stmt)

        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def add_message_with_ownership_check(
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
        """✅ 原子性地验证会话所有权并添加消息"""
        # 使用子查询验证所有权，避免额外的数据库查询
        stmt = select(ChatSession.id).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
        result = await self.db.execute(stmt)
        session_exists = result.scalar_one_or_none()

        if not session_exists:
            return None

        return await self.add_message(
            session_id=session_id,
            role=role,
            content=content,
            message_id=message_id,
            thinking=thinking,
            document_summaries=document_summaries,
            image_data_urls=image_data_urls,
            artifacts=artifacts,
            attachments=attachments,
            tool_traces=tool_traces,
            assistant_tuple_messages=assistant_tuple_messages,
            truncation_metadata=truncation_metadata,
            interruption=interruption,
        )

    async def get_session_messages(self, session_id: UUID) -> List[ChatMessage]:
        """获取会话的所有消息"""
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
    
    async def get_session_stats(self, session_id: UUID) -> Dict:
        """获取会话统计信息（消息数量和最后一条消息）"""
        # 获取消息数量
        count_stmt = select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)
        count_result = await self.db.execute(count_stmt)
        message_count = count_result.scalar() or 0
        
        # 获取最后一条消息
        last_msg_stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        last_msg_result = await self.db.execute(last_msg_stmt)
        last_message = last_msg_result.scalar_one_or_none()
        
        return {
            "messageCount": message_count,
            "lastMessage": last_message.content[:50] if last_message else ""
        }

    async def get_sessions_stats_bulk(self, session_ids: List[UUID]) -> Dict[UUID, Dict[str, Any]]:
        """批量获取会话统计信息，避免 N+1 查询。"""
        if not session_ids:
            return {}

        stats: Dict[UUID, Dict[str, Any]] = {
            session_id: {"messageCount": 0, "lastMessage": ""}
            for session_id in session_ids
        }

        count_stmt = (
            select(ChatMessage.session_id, func.count(ChatMessage.id))
            .where(ChatMessage.session_id.in_(session_ids))
            .group_by(ChatMessage.session_id)
        )
        count_result = await self.db.execute(count_stmt)
        for sid, message_count in count_result.all():
            stats[sid] = {
                "messageCount": int(message_count or 0),
                "lastMessage": stats.get(sid, {}).get("lastMessage", ""),
            }

        ranked_messages = (
            select(
                ChatMessage.session_id.label("session_id"),
                ChatMessage.content.label("content"),
                func.row_number()
                .over(
                    partition_by=ChatMessage.session_id,
                    order_by=ChatMessage.created_at.desc(),
                )
                .label("rn"),
            )
            .where(ChatMessage.session_id.in_(session_ids))
            .subquery()
        )
        latest_stmt = select(ranked_messages.c.session_id, ranked_messages.c.content).where(
            ranked_messages.c.rn == 1
        )
        latest_result = await self.db.execute(latest_stmt)
        for sid, content in latest_result.all():
            base = stats.get(sid, {"messageCount": 0, "lastMessage": ""})
            base["lastMessage"] = (content or "")[:50]
            stats[sid] = base

        return stats

    async def delete_last_assistant_message(
        self,
        session_id: UUID,
        user_id: UUID
    ) -> Optional[str]:
        """
        删除会话中最后一条 AI 回复（原子性验证所有权）
        
        Args:
            session_id: 会话ID
            user_id: 用户ID（用于验证所有权）
            
        Returns:
            被删除的消息ID，如果没有找到则返回 None
        """
        # 先验证会话所有权
        session_stmt = select(ChatSession.id).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
        session_result = await self.db.execute(session_stmt)
        if not session_result.scalar_one_or_none():
            return None
        
        # 查找最后一条 assistant 消息
        last_assistant_stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == 'assistant'
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        result = await self.db.execute(last_assistant_stmt)
        last_assistant_message = result.scalar_one_or_none()
        
        if not last_assistant_message:
            return None
        
        deleted_id = str(last_assistant_message.id)
        
        # 删除该消息
        await self.db.delete(last_assistant_message)
        
        # 更新会话的 updated_at 时间戳
        update_stmt = (
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(updated_at=datetime.utcnow())
        )
        await self.db.execute(update_stmt)
        
        await self.db.commit()
        
        return deleted_id

    @staticmethod
    def _build_message_metadata(
        document_summaries: Optional[list] = None,
        image_data_urls: Optional[list[str]] = None,
        artifacts: Optional[list[dict]] = None,
        attachments: Optional[list[dict]] = None,
        tool_traces: Optional[list[dict]] = None,
        assistant_tuple_messages: Optional[list[dict]] = None,
        truncation_metadata: Optional[dict] = None,
        interruption: Optional[dict] = None,
    ) -> Optional[dict]:
        """构建标准消息扩展信息。"""
        return build_message_metadata(
            document_summaries=document_summaries,
            image_data_urls=image_data_urls,
            artifacts=artifacts,
            attachments=attachments,
            tool_traces=tool_traces,
            assistant_tuple_messages=assistant_tuple_messages,
            truncation_metadata=truncation_metadata,
            interruption=interruption,
        )
