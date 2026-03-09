"""Token quota configuration for different membership levels."""
from typing import Dict


# User levels (matching User model)
class UserLevel:
    """User membership levels."""
    BASIC = "basic"
    MEMBER = "member"
    PREMIUM = "premium"


# Token quotas per billing cycle (tokens)
QUOTA_LIMITS: Dict[str, int] = {
    UserLevel.BASIC: 1_000_000,      # 100万 tokens - 普通用户
    UserLevel.MEMBER: 5_000_000,        # 500万 tokens - 白银会员 (测试用)
    UserLevel.PREMIUM: 10_000_000,   # 1000万 tokens - 白金会员
}

# Billing cycle duration for members (days)
# 会员计费周期为 31 天，普通用户使用自然月
MEMBER_BILLING_CYCLE_DAYS = 31

# Error messages for quota exceeded
QUOTA_EXCEEDED_MESSAGES: Dict[str, str] = {
    UserLevel.BASIC: "模型用量已达上限，请升级会员",
    UserLevel.MEMBER: "模型用量已达上限，请升级会员",
    UserLevel.PREMIUM: "模型用量已达上限，请联系管理员",
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
