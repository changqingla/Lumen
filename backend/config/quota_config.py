"""Token quota configuration and the canonical billing-window policy."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict


# User levels (matching User model)
class UserLevel:
    """User membership levels."""
    BASIC = "basic"
    MEMBER = "member"
    PREMIUM = "premium"
    ADMIN = "admin"


# Token quotas per billing cycle (tokens)
QUOTA_LIMITS: Dict[str, int] = {
    UserLevel.BASIC: 1_000_000,      # 100万 tokens - 普通用户
    UserLevel.MEMBER: 5_000_000,        # 500万 tokens - 白银会员 (测试用)
    UserLevel.PREMIUM: 10_000_000,   # 1000万 tokens - 白金会员
    UserLevel.ADMIN: 100_000_000,    # 1亿 tokens - 管理员
}

# Error messages for quota exceeded
QUOTA_EXCEEDED_MESSAGES: Dict[str, str] = {
    UserLevel.BASIC: "模型用量已达上限，请升级会员",
    UserLevel.MEMBER: "模型用量已达上限，请升级会员",
    UserLevel.PREMIUM: "模型用量已达上限，请联系管理员",
    UserLevel.ADMIN: "模型用量已达上限，请联系系统维护人员",
}


def get_quota_limit(user_level: str) -> int:
    """
    Get quota limit for a user level.
    
    Args:
        user_level: User's membership level (basic/member/premium)
        
    Returns:
        Token quota limit for the billing cycle
    """
    return QUOTA_LIMITS.get(user_level, QUOTA_LIMITS[UserLevel.BASIC])


def get_exceeded_message(user_level: str) -> str:
    """
    Get quota exceeded message for a user level.
    
    Args:
        user_level: User's membership level (basic/member/premium)
        
    Returns:
        Appropriate error message for the user level
    """
    return QUOTA_EXCEEDED_MESSAGES.get(
        user_level, 
        QUOTA_EXCEEDED_MESSAGES[UserLevel.BASIC]
    )


@dataclass(frozen=True, slots=True)
class BillingWindow:
    """A half-open UTC calendar-month billing window."""

    start: datetime
    end: datetime


def get_billing_window(now: datetime | None = None) -> BillingWindow:
    """Return the single billing-cycle definition used by quota and reporting."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return BillingWindow(start=start, end=end)


def get_effective_user_level(user: Any, now: datetime | None = None) -> str:
    """Resolve admin and expired-membership state before selecting a quota."""

    if bool(getattr(user, "is_admin", False)):
        return UserLevel.ADMIN

    level = str(getattr(user, "user_level", "") or UserLevel.BASIC).strip().lower()
    if level not in {UserLevel.BASIC, UserLevel.MEMBER, UserLevel.PREMIUM}:
        return UserLevel.BASIC
    if level == UserLevel.BASIC:
        return level

    expires_at = getattr(user, "membership_expires_at", None)
    if expires_at is None:
        return level
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return level if expires_at > current.astimezone(timezone.utc) else UserLevel.BASIC


def get_user_model_quota_limit(user: Any, now: datetime | None = None) -> int:
    """Resolve a per-user override or the effective membership-level default."""

    override = getattr(user, "model_quota_limit", None)
    if override is not None:
        return max(int(override), 0)
    return get_quota_limit(get_effective_user_level(user, now))
