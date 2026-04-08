"""Organization model for user groups."""
from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, func, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from config.database import Base
import uuid
from datetime import datetime, timezone
from typing import Optional
import secrets
import string


class Organization(Base):
    """Organization model."""
    __tablename__ = "organizations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    avatar = Column(String(255), nullable=True)
    org_code = Column(String(20), unique=True, nullable=False, index=True)
    code_expires_at = Column(DateTime(timezone=True), nullable=True)  # 组织码有效期
    owner_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    max_members = Column(Integer, default=50, nullable=False)  # 根据创建者等级设置
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)  # 软删除
    
    # 关系
    owner = relationship("User", back_populates="owned_organizations", foreign_keys=[owner_id])
    members = relationship("OrganizationMember", back_populates="organization", cascade="all, delete-orphan")
    
    def to_dict(self, include_members: bool = False):
        """Convert to dictionary."""
        # 检查关系是否已加载（避免懒加载）
        from sqlalchemy import inspect
        insp = inspect(self)
        
        # 安全地获取 owner_name
        owner_name = None
        if 'owner' not in insp.unloaded:
            # owner 已加载
            owner_name = self.owner.name if self.owner else None
        
        # 安全地获取 member_count
        member_count = 0
        if 'members' not in insp.unloaded:
            # members 已加载
            member_count = len(self.members) if self.members else 0
        
        result = {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "avatar": self.avatar,
            "org_code": self.org_code,
            "code_expires_at": self.code_expires_at.isoformat() if self.code_expires_at else None,
            "owner_id": str(self.owner_id),
            "owner_name": owner_name,
            "max_members": self.max_members,
            "member_count": member_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        
        if include_members and 'members' not in insp.unloaded and self.members:
            result["members"] = [m.to_dict() for m in self.members]
        
        return result
    
    # === 辅助方法 ===
    
    def is_code_expired(self) -> bool:
        """检查组织码是否过期"""
        if self.code_expires_at is None:
            return False  # 永久有效
        return self.code_expires_at <= datetime.now(timezone.utc)
    
    def can_add_member(self) -> tuple[bool, Optional[str]]:
        """
        检查是否可以添加新成员
        返回: (是否可以, 原因)
        
        注意：此方法需要 members 关系已加载
        """
        from sqlalchemy import inspect
        insp = inspect(self)
        
        if 'members' in insp.unloaded:
            # members 未加载，无法检查，返回谨慎的结果
            return True, None
        
        current_count = len(self.members) if self.members else 0
        
        if current_count >= self.max_members:
            return False, f"组织成员已达上限（{self.max_members}人）"
        
        return True, None
    
    def is_owner(self, user_id: uuid.UUID) -> bool:
        """检查指定用户是否为组织所有者"""
        return self.owner_id == user_id
    
    def is_member(self, user_id: uuid.UUID) -> bool:
        """
        检查指定用户是否为组织成员
        
        注意：此方法需要 members 关系已加载
        """
        from sqlalchemy import inspect
        insp = inspect(self)
        
        if 'members' in insp.unloaded:
            # members 未加载，无法检查
            return False
        
        if not self.members:
            return False
        return any(m.user_id == user_id for m in self.members)
    
    def get_member_role(self, user_id: uuid.UUID) -> Optional[str]:
        """
        获取用户在组织中的角色
        
        注意：此方法需要 members 关系已加载
        """
        from sqlalchemy import inspect
        insp = inspect(self)
        
        if 'members' in insp.unloaded:
            # members 未加载，无法检查
            return None
        
        if not self.members:
            return None
        
        for m in self.members:
            if m.user_id == user_id:
                return m.role
        
        return None
    
    @staticmethod
    def generate_org_code(length: int = 8) -> str:
        """
        生成随机组织码
        
        Args:
            length: 组织码长度，默认8位
            
        Returns:
            随机组织码字符串
        """
        # 使用大写字母和数字，排除易混淆的字符
        chars = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '')
        code = ''.join(secrets.choice(chars) for _ in range(length))
        return code

