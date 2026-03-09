"""Quota service for managing token usage quotas based on membership levels."""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from config.quota_config import (
    QUOTA_LIMITS,
    MEMBER_BILLING_CYCLE_DAYS,
    QUOTA_EXCEEDED_MESSAGES,
    UserLevel,
)
from models.user import User
from services.token_usage_service import TokenUsageService

logger = logging.getLogger(__name__)


@dataclass
class QuotaStatus:
    """配额状态数据类"""
    user_level: str
    quota_limit: int
    used_tokens: int
    remaining_tokens: int
    is_exceeded: bool
    billing_cycle_start: datetime
    billing_cycle_end: datetime
    exceeded_message: Optional[str] = None


class QuotaService:
    """Service for managing token usage quotas."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def get_quota_limit(self, user: User) -> int:
        """
        获取用户的配额限制
        
        Args:
            user: User model instance
            
        Returns:
            Token quota limit for the user's level
        """
        return QUOTA_LIMITS.get(user.user_level, QUOTA_LIMITS[UserLevel.BASIC])
    
    def get_billing_cycle(
        self, 
        user: User, 
        reference_date: Optional[datetime] = None
    ) -> Tuple[datetime, datetime]:
        """
        计算用户的计费周期
        
        普通用户: 自然月 (1号 00:00:00 - 下月1号 00:00:00)
        会员用户: 激活日起 31 天为一个周期
        
        Args:
            user: User model instance
            reference_date: Reference date for calculation (default: now)
            
        Returns:
            Tuple of (cycle_start, cycle_end) datetimes
        """
        now = reference_date or datetime.now(timezone.utc)
        
        # 确保 now 有时区信息
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        
        # 检查是否为有效会员
        if user.user_level in (UserLevel.MEMBER, UserLevel.PREMIUM) and user.membership_expires_at:
            # 确保过期时间有时区信息
            expires_at = user.membership_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            
            # 检查会员是否已过期
            if expires_at <= now:
                # 会员已过期，使用自然月
                return self._calculate_natural_month_cycle(now)
            
            # 会员用户: 基于激活日期计算 31 天周期
            return self._calculate_member_billing_cycle(expires_at, now)
        else:
            # 普通用户: 自然月
            return self._calculate_natural_month_cycle(now)

    
    def _calculate_natural_month_cycle(self, now: datetime) -> Tuple[datetime, datetime]:
        """
        计算自然月周期
        
        Args:
            now: Reference datetime
            
        Returns:
            Tuple of (cycle_start, cycle_end) where:
            - cycle_start: 1st day of current month at 00:00:00
            - cycle_end: 1st day of next month at 00:00:00
        """
        # 确保有时区信息
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        
        # 当月1号 00:00:00
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # 下个月1号 00:00:00
        if now.month == 12:
            end = start.replace(year=now.year + 1, month=1)
        else:
            end = start.replace(month=now.month + 1)
        
        return start, end
    
    def _calculate_member_billing_cycle(
        self, 
        expires_at: datetime, 
        now: datetime
    ) -> Tuple[datetime, datetime]:
        """
        计算会员计费周期 (31天为一个周期)
        
        从会员过期日期反推当前所在的计费周期。
        
        Args:
            expires_at: Membership expiration datetime
            now: Reference datetime
            
        Returns:
            Tuple of (cycle_start, cycle_end) where cycle duration is 31 days
        """
        # 确保有时区信息
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        
        cycle_days = MEMBER_BILLING_CYCLE_DAYS
        
        # 计算从现在到过期日期的天数
        days_until_expiry = (expires_at - now).days
        
        if days_until_expiry < 0:
            # 已过期，使用自然月
            return self._calculate_natural_month_cycle(now)
        
        # 从过期日期反推当前周期
        # 过期日期是某个周期的结束点
        # 计算当前时间在哪个周期内
        
        # 计算从过期日期往回数，now 在第几个周期
        # cycles_back = 0 表示当前周期结束于 expires_at
        # cycles_back = 1 表示当前周期结束于 expires_at - 31 days
        cycles_back = days_until_expiry // cycle_days
        
        # 当前周期的结束日
        current_cycle_end = expires_at - timedelta(days=cycles_back * cycle_days)
        # 当前周期的开始日
        current_cycle_start = current_cycle_end - timedelta(days=cycle_days)
        
        return current_cycle_start, current_cycle_end
    
    async def check_quota(self, user: User) -> QuotaStatus:
        """
        检查用户配额状态
        
        Args:
            user: User model instance
            
        Returns:
            QuotaStatus with current quota information
        """
        quota_limit = self.get_quota_limit(user)
        cycle_start, cycle_end = self.get_billing_cycle(user)
        
        # 获取当前周期内的使用量
        usage_service = TokenUsageService(self.db)
        usage = await usage_service.get_total_usage(
            str(user.id),
            start_time=cycle_start,
            end_time=cycle_end
        )
        
        used_tokens = usage.get("total_tokens", 0)
        remaining = max(0, quota_limit - used_tokens)
        is_exceeded = used_tokens >= quota_limit
        
        exceeded_message = None
        if is_exceeded:
            exceeded_message = QUOTA_EXCEEDED_MESSAGES.get(
                user.user_level, 
                QUOTA_EXCEEDED_MESSAGES[UserLevel.BASIC]
            )
        
        return QuotaStatus(
            user_level=user.user_level,
            quota_limit=quota_limit,
            used_tokens=used_tokens,
            remaining_tokens=remaining,
            is_exceeded=is_exceeded,
            billing_cycle_start=cycle_start,
            billing_cycle_end=cycle_end,
            exceeded_message=exceeded_message
        )
