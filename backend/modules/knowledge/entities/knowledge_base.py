"""Knowledge Base database model with visibility and organization sharing support."""
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Boolean, func, UniqueConstraint, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from config.database import Base
import uuid
from typing import List, Optional


# 知识库分类常量
KNOWLEDGE_CATEGORIES = [
    "工学", "理学", "法学", "文学", "教育学", "经济学",
    "历史学", "哲学", "农学", "医学", "管理学", "艺术学", "其它"
]


class KnowledgeBase(Base):
    """Knowledge Base model - supports private, organization, and public sharing."""
    __tablename__ = "knowledge_bases"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    category = Column(String(50), nullable=False, default="其它", index=True)  # 知识库分类
    is_public = Column(Boolean, nullable=False, default=False, index=True)  # 是否公开（旧字段，保持兼容）
    
    # 新增可见性控制字段
    visibility = Column(String(20), nullable=False, default='private', index=True)  # private/organization/public
    shared_to_orgs = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)  # 共享到的组织ID列表
    
    subscribers_count = Column(Integer, nullable=False, default=0, index=True)  # 订阅数
    view_count = Column(Integer, nullable=False, default=0)  # 浏览量
    contents_count = Column(Integer, nullable=False, default=0)
    avatar = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_updated_at = Column(DateTime(timezone=True), nullable=True)  # 最后内容更新时间
    
    def to_dict(self, include_owner=False):
        """Convert to dictionary."""
        result = {
            "id": str(self.id),
            "name": self.name,
            "description": self.description or "",
            "category": self.category,
            "isPublic": self.is_public,  # 保持兼容
            "visibility": self.visibility,
            "shared_to_orgs": [str(org_id) for org_id in (self.shared_to_orgs or [])],
            "subscribersCount": self.subscribers_count,
            "viewCount": self.view_count,
            "contents": self.contents_count,
            "avatar": self.avatar or "/kb.png",
            "createdAt": self.created_at.strftime("%Y-%m-%d"),
            "updatedAt": self.updated_at.strftime("%Y-%m-%d"),
        }
        if include_owner:
            result["ownerId"] = str(self.owner_id)
        return result
    
    # === 辅助方法 ===
    
    def is_visible_to_user(self, user_id: uuid.UUID, user_org_ids: List[uuid.UUID], is_admin: bool = False) -> bool:
        """
        检查知识库对指定用户是否可见
        
        Args:
            user_id: 用户ID
            user_org_ids: 用户所在的组织ID列表
            is_admin: 用户是否为管理员
            
        Returns:
            是否可见
        """
        # 所有者总是可见
        if self.owner_id == user_id:
            return True
        
        # 管理员总是可见
        if is_admin:
            return True
        
        # public: 全局可见（仅管理员可设置）
        if self.visibility == 'public':
            return True
        
        # organization: 组织可见
        if self.visibility == 'organization':
            # 检查用户是否在共享的组织中
            if not self.shared_to_orgs:
                return False
            return any(org_id in self.shared_to_orgs for org_id in user_org_ids)
        
        # private: 仅所有者可见
        return False
    
    def share_to_organizations(self, org_ids: List[uuid.UUID]) -> None:
        """
        共享知识库到指定组织
        
        Args:
            org_ids: 组织ID列表
        """
        self.visibility = 'organization'
        self.shared_to_orgs = org_ids
    
    def set_public(self, is_public: bool) -> None:
        """
        设置知识库公开状态（仅管理员）
        
        Args:
            is_public: 是否公开
        """
        if is_public:
            self.visibility = 'public'
            self.is_public = True  # 保持兼容
        else:
            self.visibility = 'private'
            self.is_public = False
            self.shared_to_orgs = []
    
    def set_private(self) -> None:
        """设置为私有"""
        self.visibility = 'private'
        self.is_public = False
        self.shared_to_orgs = []


class KnowledgeBaseSubscription(Base):
    """Knowledge Base subscription relationship."""
    __tablename__ = "kb_subscriptions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    subscribed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (
        UniqueConstraint('user_id', 'kb_id', name='uq_user_kb_subscription'),
    )
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "userId": str(self.user_id),
            "kbId": str(self.kb_id),
            "subscribedAt": self.subscribed_at.isoformat(),
            "lastViewedAt": self.last_viewed_at.isoformat() if self.last_viewed_at else None,
        }

