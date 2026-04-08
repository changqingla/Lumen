"""Organization-related quota configuration."""

from typing import Any, Dict

from config.quota_config import UserLevel

# 用户配额定义
USER_QUOTAS: Dict[str, Dict[str, Any]] = {
    UserLevel.BASIC: {
        "create_org": 0,       # 不能创建组织
        "join_org": 1,         # 最多加入1个组织
        "org_members": 0,      # 不能创建组织，所以成员数为0
        "kb_count": 3,         # 最多3个知识库
        "kb_storage_mb": 100,  # 100MB存储
    },
    UserLevel.MEMBER: {
        "create_org": 1,       # 可以创建1个组织
        "join_org": 3,         # 最多加入3个组织
        "org_members": 100,    # 组织最多100个成员
        "kb_count": 10,        # 最多10个知识库
        "kb_storage_mb": 1000, # 1GB存储
    },
    UserLevel.PREMIUM: {
        "create_org": 2,        # 可以创建2个组织
        "join_org": 10,         # 最多加入10个组织
        "org_members": 500,     # 组织最多500个成员
        "kb_count": 50,         # 最多50个知识库
        "kb_storage_mb": 10000, # 10GB存储
    },
    UserLevel.ADMIN: {
        "create_org": -1,      # 无限制（-1 表示无限制）
        "join_org": -1,        # 无限制（-1表示无限制）
        "org_members": -1,     # 无限制成员数
        "kb_count": -1,        # 无限制知识库
        "kb_storage_mb": -1,   # 无限制存储
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


def can_create_organization(user_level: str, current_org_count: int) -> bool:
    """
    检查用户是否可以创建更多组织。
    
    Args:
        user_level: 用户等级
        current_org_count: 当前已创建的组织数量
    
    Returns:
        是否可以创建
    """
    quota = get_user_quota(user_level)
    max_create = quota['create_org']
    
    if max_create == -1:  # 无限制
        return True
    
    return current_org_count < max_create


def can_join_organization(user_level: str, current_joined_count: int) -> bool:
    """
    检查用户是否可以加入更多组织。
    
    Args:
        user_level: 用户等级
        current_joined_count: 当前已加入的组织数量
    
    Returns:
        是否可以加入
    """
    quota = get_user_quota(user_level)
    max_join = quota['join_org']
    
    if max_join == -1:  # 无限制
        return True
    
    return current_joined_count < max_join


def get_org_member_limit(user_level: str) -> int:
    """
    获取组织成员上限。
    
    Args:
        user_level: 用户等级
    
    Returns:
        成员上限数量（-1表示无限制）
    """
    quota = get_user_quota(user_level)
    return quota['org_members']


def can_create_kb(user_level: str, current_kb_count: int) -> bool:
    """
    检查用户是否可以创建更多知识库。
    
    Args:
        user_level: 用户等级
        current_kb_count: 当前知识库数量
    
    Returns:
        是否可以创建
    """
    quota = get_user_quota(user_level)
    max_kb = quota['kb_count']
    
    if max_kb == -1:  # 无限制
        return True
    
    return current_kb_count < max_kb


def get_storage_limit_mb(user_level: str) -> int:
    """
    获取存储空间限制（MB）。
    
    Args:
        user_level: 用户等级
    
    Returns:
        存储限制（MB，-1表示无限制）
    """
    quota = get_user_quota(user_level)
    return quota['kb_storage_mb']


def check_storage_quota(user_level: str, current_usage_mb: float, additional_mb: float) -> bool:
    """
    检查存储配额是否足够。
    
    Args:
        user_level: 用户等级
        current_usage_mb: 当前使用量（MB）
        additional_mb: 需要增加的用量（MB）
    
    Returns:
        配额是否足够
    """
    limit = get_storage_limit_mb(user_level)
    
    if limit == -1:  # 无限制
        return True
    
    return (current_usage_mb + additional_mb) <= limit
