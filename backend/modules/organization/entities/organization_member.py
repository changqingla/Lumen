"""Organization member model for managing user-organization relationships."""
from sqlalchemy import Column, String, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from config.database import Base
import uuid


class OrganizationMember(Base):
    """Organization member model."""
    __tablename__ = "organization_members"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    role = Column(String(20), default='member', nullable=False, index=True)  # 'owner', 'member'
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    
    # 唯一约束：同一用户不能重复加入同一组织
    __table_args__ = (
        UniqueConstraint('org_id', 'user_id', name='uq_org_user'),
    )
    
    # 关系
    organization = relationship("Organization", back_populates="members")
    user = relationship("User", back_populates="organization_memberships")
    
    def to_dict(self):
        """Convert to dictionary."""
        # 检查关系是否已加载（避免懒加载）
        from sqlalchemy import inspect
        insp = inspect(self)
        
        # 安全地获取 organization 信息
        org_name = None
        if 'organization' not in insp.unloaded:
            org_name = self.organization.name if self.organization else None
        
        # 安全地获取 user 信息
        user_name = None
        user_email = None
        user_avatar = None
        if 'user' not in insp.unloaded:
            if self.user:
                user_name = self.user.name
                user_email = self.user.email
                user_avatar = self.user.avatar
        
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "org_name": org_name,
            "user_id": str(self.user_id),
            "user_name": user_name,
            "user_email": user_email,
            "user_avatar": user_avatar,
            "role": self.role,
            "joined_at": self.joined_at.isoformat() if self.joined_at else None,
        }
    
    # === 辅助方法 ===
    
    def is_owner(self) -> bool:
        """检查是否为组织所有者"""
        return self.role == 'owner'
    
    def is_member(self) -> bool:
        """检查是否为普通成员"""
        return self.role == 'member'

