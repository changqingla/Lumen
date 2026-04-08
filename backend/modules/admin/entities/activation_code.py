"""Activation code model for membership activation."""
from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from config.database import Base
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
import secrets
import string


class ActivationCode(Base):
    """Activation code model for membership."""
    __tablename__ = "activation_codes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)
    type = Column(String(20), nullable=False, index=True)  # 'member', 'premium'
    duration_days = Column(Integer, nullable=True)  # NULL = 永久有效
    max_usage = Column(Integer, default=1, nullable=False)
    used_count = Column(Integer, default=0, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)  # 激活码本身的有效期
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # 关系
    creator = relationship("User", foreign_keys=[created_by])
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "code": self.code,
            "type": self.type,
            "duration_days": self.duration_days,
            "max_usage": self.max_usage,
            "used_count": self.used_count,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
            "is_valid": self.is_valid(),
        }
    
    # === 辅助方法 ===
    
    def _get_expires_at_aware(self) -> Optional[datetime]:
        """获取带时区的过期时间"""
        if self.expires_at is None:
            return None
        if self.expires_at.tzinfo is None:
            return self.expires_at.replace(tzinfo=timezone.utc)
        return self.expires_at
    
    def is_valid(self) -> bool:
        """检查激活码是否有效（综合检查）"""
        # 检查是否被作废
        if not self.is_active:
            return False
        
        # 检查是否过期
        expires_at = self._get_expires_at_aware()
        if expires_at and expires_at <= datetime.now(timezone.utc):
            return False
        
        # 检查使用次数
        if self.used_count >= self.max_usage:
            return False
        
        return True
    
    def can_use(self) -> tuple[bool, Optional[str]]:
        """
        检查激活码是否可以使用
        返回: (是否可用, 原因)
        """
        if not self.is_active:
            return False, "激活码已被作废"
        
        expires_at = self._get_expires_at_aware()
        if expires_at and expires_at <= datetime.now(timezone.utc):
            return False, "激活码已过期"
        
        if self.used_count >= self.max_usage:
            return False, "激活码使用次数已达上限"
        
        return True, None
    
    def use(self) -> None:
        """使用激活码（增加使用次数）"""
        self.used_count += 1
    
    def get_membership_expiry_date(self, current_expiry: Optional[datetime] = None) -> Optional[datetime]:
        """
        计算激活后的会员到期时间
        
        Args:
            current_expiry: 当前会员到期时间（如果已经是会员）
            
        Returns:
            新的到期时间，如果 duration_days 为 NULL 则返回 None（表示永久）
        """
        if self.duration_days is None:
            return None  # 永久会员
        
        now = datetime.now(timezone.utc)
        
        # 如果当前已经是会员且未过期，从当前到期时间开始计算
        if current_expiry:
            # 确保时区一致性
            expiry = current_expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > now:
                return expiry + timedelta(days=self.duration_days)
        
        # 否则从现在开始计算
        return now + timedelta(days=self.duration_days)
    
    @staticmethod
    def generate_code(length: int = 12) -> str:
        """
        生成随机激活码
        
        Args:
            length: 激活码长度，默认12位
            
        Returns:
            随机激活码字符串
        """
        # 使用大写字母和数字，排除易混淆的字符（0O, 1lI等）
        chars = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '')
        code = ''.join(secrets.choice(chars) for _ in range(length))
        return code

