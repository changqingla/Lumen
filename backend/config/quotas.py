"""Organization-related quota configuration."""

from typing import Any, Dict

from config.quota_config import UserLevel

# 用户配额定义
USER_QUOTAS: Dict[str, Dict[str, Any]] = {
    UserLevel.BASIC: {
        "create_org": 0,       # 不能创建组织
        "join_org": 1,         # 最多加入1个组织
        "org_members": 0,      # 不能创建组织，所以成员数为0
    },
    UserLevel.MEMBER: {
        "create_org": 1,       # 可以创建1个组织
        "join_org": 3,         # 最多加入3个组织
        "org_members": 100,    # 组织最多100个成员
    },
    UserLevel.PREMIUM: {
        "create_org": 2,        # 可以创建2个组织
        "join_org": 10,         # 最多加入10个组织
        "org_members": 500,     # 组织最多500个成员
    },
    UserLevel.ADMIN: {
        "create_org": -1,      # 无限制（-1 表示无限制）
        "join_org": -1,        # 无限制（-1表示无限制）
        "org_members": -1,     # 无限制成员数
    },
}


def get_user_quota(user_level: str) -> Dict[str, Any]:
    """
    获取用户配额。
    
    Args:
        user_level: 用户等级 (basic/member/premium/admin)
    
    Returns:
        用户配额字典
    """
    return USER_QUOTAS.get(user_level, USER_QUOTAS[UserLevel.BASIC])
