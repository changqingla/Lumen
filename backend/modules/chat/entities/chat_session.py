"""
聊天会话模型
"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import uuid

from config.database import Base
from modules.chat.message_metadata import ParsedMessageMetadata, parse_message_metadata

logger = logging.getLogger(__name__)


class ChatSession(Base):
    """聊天会话"""
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)  # 会话标题（通常是第一条消息的摘要）
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # 会话配置元数据
    config = Column(JSONB, nullable=True)  # 存储会话配置的 JSON 对象
    # config 结构示例:
    # {
    #   "uiMode": "normal",              # 交互模式: "normal" | "plan"
    #   "kbIds": ["uuid1", "uuid2"],      # 知识库ID列表
    #   "docIds": ["uuid3", "uuid4"],     # 文档ID列表
    #   "sourceType": "home",             # 来源: "home" | "knowledge" | "favorites"
    #   "isKBLocked": true,               # 知识库是否已锁定
    #   "modelName": "qwen3.5-flash",    # 当前会话选择的模型目录名
    #   "deepThinking": true              # 是否启用深度思考
    # }
    
    # 关系（使用 lazy loading）
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan", lazy="select")
    user = relationship("User", back_populates="chat_sessions", lazy="select")

    def to_dict(self, include_messages=False):
        """转换为字典"""
        result = {
            "id": str(self.id),
            "title": self.title,
            "lastMessage": "",
            "timestamp": self._format_timestamp(self.updated_at),
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "messageCount": 0,
            "config": self.config or {}  # 添加配置信息
        }
        
        # 只在明确需要时才访问关系属性
        if include_messages:
            try:
                messages_list = list(self.messages)
                if messages_list:
                    last_message = messages_list[-1]
                    result["lastMessage"] = last_message.content[:50]
                    result["messageCount"] = len(messages_list)
            except Exception as exc:
                logger.warning(
                    "Failed to load messages while serializing session %s: %s",
                    self.id,
                    exc,
                )
        
        return result
    
    @staticmethod
    def _format_timestamp(dt: datetime) -> str:
        """格式化时间戳为相对时间"""
        now = datetime.utcnow()
        diff = now - dt
        
        if diff.days > 7:
            return f"{diff.days} 天前"
        elif diff.days > 0:
            return f"{diff.days} 天前"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            return f"{hours} 小时前"
        elif diff.seconds >= 60:
            minutes = diff.seconds // 60
            return f"{minutes} 分钟前"
        else:
            return "刚刚"


class ChatMessage(Base):
    """聊天消息"""
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 'user' | 'assistant'
    content = Column(Text, nullable=False)
    thinking = Column(Text, nullable=True)  # AI 的思考过程
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # 消息扩展信息：
    # {"document_summaries": [...], "image_data_urls": [...], "artifacts": [...], "attachments": [...], "truncation": {...}}
    document_summaries = Column(JSONB, nullable=True)
    
    # 关系
    session = relationship("ChatSession", back_populates="messages")

    @staticmethod
    def _parse_message_metadata(raw: Any) -> ParsedMessageMetadata:
        """解析消息扩展信息。"""
        return parse_message_metadata(raw)

    def to_dict(self):
        """转换为字典"""
        (
            document_summaries,
            image_data_urls,
            truncation,
            artifacts,
            attachments,
            tool_traces,
            assistant_tuple_messages,
            interruption,
        ) = self._parse_message_metadata(
            self.document_summaries,
        )
        return {
            "id": str(self.id),
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "createdAt": self.created_at.isoformat(),
            "documentSummaries": document_summaries,
            "imageDataUrls": image_data_urls,
            "artifacts": artifacts,
            "attachments": attachments,
            "toolTraces": tool_traces,
            "assistantTupleMessages": assistant_tuple_messages,
            "wasTruncated": bool(truncation and truncation.get("was_truncated")),
            "truncatedAt": truncation.get("truncated_at") if truncation else None,
            "interruption": {
                "reason": interruption.get("reason"),
                "interruptedAt": interruption.get("interrupted_at"),
                "retryable": interruption.get("retryable", True),
            } if interruption else None,
        }
