"""User database model with membership and admin support."""
from sqlalchemy import BigInteger, Column, String, DateTime, Boolean, ARRAY, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from config.database import Base
from config.quota_config import UserLevel
from config.quotas import get_user_quota
import uuid
from datetime import datetime, timezone
from typing import Optional


class User(Base):
    """User model with membership levels and admin support."""
    __tablename__ = "users"
    
    # 基本字段
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False, unique=True, index=True)  # 添加唯一约束
    avatar = Column(String, nullable=True)
    
    # 会员和等级相关字段
    user_level = Column(String(20), default=UserLevel.BASIC, nullable=False, index=True)  # basic/member/premium
    membership_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    activated_codes = Column(ARRAY(String), default=list, nullable=False)  # 已激活的激活码列表
    is_admin = Column(Boolean, default=False, nullable=False, index=True)
    model_quota_limit = Column(BigInteger, nullable=True)  # 模型 token 配额覆盖值（为空则按等级配额）
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # 关系
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    owned_organizations = relationship("Organization", back_populates="owner", foreign_keys="Organization.owner_id")
    organization_memberships = relationship("OrganizationMember", back_populates="user", cascade="all, delete-orphan")
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "email": self.email,
            "name": self.name,
            "avatar": self.avatar,
            "user_level": self.user_level,
            "is_member": self.is_member(),
            "is_advanced_member": self.is_premium(),
            "is_admin": self.is_admin,
            "model_quota_limit": self.model_quota_limit,
            "member_expires_at": self.membership_expires_at.isoformat() if self.membership_expires_at else None,
        }
    
    # === 辅助方法 ===
    
    def is_member(self) -> bool:
        """检查用户是否为会员（包括高级会员）"""
        if self.user_level in (UserLevel.MEMBER, UserLevel.PREMIUM):
            # 检查是否过期
            if self.membership_expires_at is None:
                return True  # 永久会员
            # 确保时区一致性比较
            expires_at = self.membership_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return expires_at > datetime.now(timezone.utc)
        return False
    
    def is_premium(self) -> bool:
        """检查用户是否为高级会员"""
        if self.user_level == UserLevel.PREMIUM:
            # 检查是否过期
            if self.membership_expires_at is None:
                return True
            # 确保时区一致性比较
            expires_at = self.membership_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return expires_at > datetime.now(timezone.utc)
        return False
    
    def is_membership_expired(self) -> bool:
        """检查会员是否已过期"""
        if self.membership_expires_at is None:
            return False  # 永久会员不会过期
        # 确保时区一致性比较
        expires_at = self.membership_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= datetime.now(timezone.utc)
    
    def can_create_organization(self) -> tuple[bool, Optional[str]]:
        """
        检查用户是否可以创建组织
        返回: (是否可以, 原因)
        """
        # 管理员无限制
        if self.is_admin:
            return True, None
        
        # 普通用户不能创建
        if self.user_level == UserLevel.BASIC:
            return False, "普通用户无法创建组织，请先升级为会员"
        
        # 检查会员是否过期
        if self.is_membership_expired():
            return False, "您的会员已过期，无法创建组织"
        
        # 检查已创建的组织数量（在service层实现具体逻辑）
        return True, None
    
    def get_organization_limits(self) -> dict:
        """
        获取用户的组织限制
        返回: {
            'can_create': int,  # 可创建组织数
            'can_join': int,    # 可加入组织数
            'max_members': int  # 创建的组织最大成员数
        }
        """
        if self.is_admin:
            return {
                "can_create": float("inf"),
                "can_join": float("inf"),
                "max_members": float("inf"),
            }

        quota = get_user_quota(self.user_level or UserLevel.BASIC)
        return {
            "can_create": quota["create_org"],
            "can_join": quota["join_org"],
            "max_members": quota["org_members"],
        }
