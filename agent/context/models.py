"""
上下文管理数据模型

定义会话、消息、压缩记录等核心数据结构
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from uuid import uuid4


class MessageType(Enum):
    """消息类型"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    COMPRESSION = "compression"  # 压缩摘要消息


class SessionStatus(Enum):
    """会话状态"""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass
class Message:
    """消息数据模型"""
    message_id: str
    session_id: str
    role: str  # "user" or "assistant"
    content: str
    token_count: int
    created_at: datetime
    message_type: MessageType = MessageType.USER
    is_compressed: bool = False
    compression_id: Optional[str] = None
    sequence_number: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_user_message(
        cls,
        session_id: str,
        content: str,
        token_count: int,
        sequence_number: Optional[int] = None
    ) -> "Message":
        """
        创建用户消息
        
        Args:
            session_id: 会话ID
            content: 消息内容
            token_count: token数
            sequence_number: 序号，如果为None则由storage层自动分配
        """
        return cls(
            message_id=f"msg_{uuid4().hex}",
            session_id=session_id,
            role="user",
            content=content,
            token_count=token_count,
            created_at=datetime.now(),
            message_type=MessageType.USER,
            sequence_number=sequence_number if sequence_number is not None else 0
        )

    @classmethod
    def create_assistant_message(
        cls,
        session_id: str,
        content: str,
        token_count: int,
        sequence_number: Optional[int] = None
    ) -> "Message":
        """
        创建助手消息
        
        Args:
            session_id: 会话ID
            content: 消息内容
            token_count: token数
            sequence_number: 序号，如果为None则由storage层自动分配
        """
        return cls(
            message_id=f"msg_{uuid4().hex}",
            session_id=session_id,
            role="assistant",
            content=content,
            token_count=token_count,
            created_at=datetime.now(),
            message_type=MessageType.ASSISTANT,
            sequence_number=sequence_number if sequence_number is not None else 0
        )

    @classmethod
    def create_compression_message(
        cls,
        session_id: str,
        content: str,
        token_count: int,
        compression_id: str,
        sequence_number: int
    ) -> "Message":
        """创建压缩摘要消息"""
        return cls(
            message_id=f"msg_{uuid4().hex}",
            session_id=session_id,
            role="system",
            content=content,
            token_count=token_count,
            created_at=datetime.now(),
            message_type=MessageType.COMPRESSION,
            compression_id=compression_id,
            sequence_number=sequence_number
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "message_id": self.message_id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "token_count": self.token_count,
            "created_at": self.created_at.isoformat(),
            "message_type": self.message_type.value,
            "is_compressed": self.is_compressed,
            "compression_id": self.compression_id,
            "sequence_number": self.sequence_number,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """从字典创建"""
        return cls(
            message_id=data["message_id"],
            session_id=data["session_id"],
            role=data["role"],
            content=data["content"],
            token_count=data["token_count"],
            created_at=datetime.fromisoformat(data["created_at"]),
            message_type=MessageType(data["message_type"]),
            is_compressed=data.get("is_compressed", False),
            compression_id=data.get("compression_id"),
            sequence_number=data.get("sequence_number", 0),
            metadata=data.get("metadata", {})
        )


@dataclass
class Session:
    """
    会话数据模型
    
    字段说明:
        session_id: 会话唯一标识
        user_id: 用户标识
        created_at: 创建时间
        updated_at: 最后更新时间
        total_token_count: 所有活跃消息的token总数（包括压缩摘要，不包括已被压缩的原始消息）
        message_count: 用户交互消息数（user + assistant消息数，不包括系统生成的压缩摘要）
        compression_count: 压缩执行次数
        status: 会话状态（active/archived/deleted）
        metadata: 额外元数据
    """
    session_id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    total_token_count: int = 0
    message_count: int = 0  # 用户交互消息数（不包括压缩摘要）
    compression_count: int = 0
    status: SessionStatus = SessionStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_new(cls, user_id: str, session_id: Optional[str] = None) -> "Session":
        """
        创建新会话
        
        Args:
            user_id: 用户ID
            session_id: 可选的会话ID，如果不提供则自动生成
            
        Returns:
            创建的会话对象
        """
        now = datetime.now()
        return cls(
            session_id=session_id or f"session_{uuid4().hex}",
            user_id=user_id,
            created_at=now,
            updated_at=now,
            status=SessionStatus.ACTIVE
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "total_token_count": self.total_token_count,
            "message_count": self.message_count,
            "compression_count": self.compression_count,
            "status": self.status.value,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """从字典创建"""
        return cls(
            session_id=data["session_id"],
            user_id=data["user_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            total_token_count=data.get("total_token_count", 0),
            message_count=data.get("message_count", 0),
            compression_count=data.get("compression_count", 0),
            status=SessionStatus(data.get("status", "active")),
            metadata=data.get("metadata", {})
        )


@dataclass
class CompressionRecord:
    """压缩记录数据模型"""
    compression_id: str
    session_id: str
    round: int
    original_message_count: int
    compressed_token_count: int
    summary_token_count: int
    summary_content: str
    compressed_message_ids: List[str]
    created_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_new(
        cls,
        session_id: str,
        round: int,
        original_message_count: int,
        compressed_token_count: int,
        summary_token_count: int,
        summary_content: str,
        compressed_message_ids: List[str]
    ) -> "CompressionRecord":
        """创建新的压缩记录"""
        return cls(
            compression_id=f"comp_{uuid4().hex}",
            session_id=session_id,
            round=round,
            original_message_count=original_message_count,
            compressed_token_count=compressed_token_count,
            summary_token_count=summary_token_count,
            summary_content=summary_content,
            compressed_message_ids=compressed_message_ids,
            created_at=datetime.now()
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "compression_id": self.compression_id,
            "session_id": self.session_id,
            "round": self.round,
            "original_message_count": self.original_message_count,
            "compressed_token_count": self.compressed_token_count,
            "summary_token_count": self.summary_token_count,
            "summary_content": self.summary_content,
            "compressed_message_ids": self.compressed_message_ids,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompressionRecord":
        """从字典创建"""
        return cls(
            compression_id=data["compression_id"],
            session_id=data["session_id"],
            round=data["round"],
            original_message_count=data["original_message_count"],
            compressed_token_count=data["compressed_token_count"],
            summary_token_count=data["summary_token_count"],
            summary_content=data["summary_content"],
            compressed_message_ids=data["compressed_message_ids"],
            created_at=datetime.fromisoformat(data["created_at"]),
            metadata=data.get("metadata", {})
        )

    @property
    def compression_ratio(self) -> float:
        """计算压缩比"""
        if self.compressed_token_count == 0:
            return 0.0
        return 1.0 - (self.summary_token_count / self.compressed_token_count)

    @property
    def saved_tokens(self) -> int:
        """计算节省的token数"""
        return self.compressed_token_count - self.summary_token_count

