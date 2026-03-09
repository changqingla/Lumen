"""Custom exceptions for the application."""

from fastapi import HTTPException, status
from typing import Optional, Dict, Any


class BaseAPIException(HTTPException):
    """基础API异常类"""
    
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        detail = {
            "error": {
                "code": error_code,
                "message": message
            }
        }
        if details:
            detail["error"]["details"] = details
        
        super().__init__(status_code=status_code, detail=detail)


# ============ Organization Related Exceptions ============

class OrganizationLimitExceeded(BaseAPIException):
    """组织数量超限"""
    
    def __init__(self, max_count: int, message: Optional[str] = None):
        if message is None:
            message = f"您已达到创建组织的上限（{max_count}个）"
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="ORGANIZATION_LIMIT_EXCEEDED",
            message=message,
            details={"max_count": max_count}
        )


class OrganizationMemberLimitExceeded(BaseAPIException):
    """组织成员超限"""
    
    def __init__(self, max_members: int, message: Optional[str] = None):
        if message is None:
            message = f"组织成员数量已达上限（{max_members}人）"
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="ORGANIZATION_MEMBER_LIMIT_EXCEEDED",
            message=message,
            details={"max_members": max_members}
        )


class OrgCodeExpired(BaseAPIException):
    """组织码过期"""
    
    def __init__(self, message: str = "组织邀请码已过期"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="ORG_CODE_EXPIRED",
            message=message
        )


class OrgCodeInvalid(BaseAPIException):
    """组织码无效"""
    
    def __init__(self, message: str = "组织邀请码不存在或无效"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="ORG_CODE_INVALID",
            message=message
        )


class NotOrganizationOwner(BaseAPIException):
    """非组织所有者"""
    
    def __init__(self, message: str = "只有组织所有者可以执行此操作"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="NOT_ORGANIZATION_OWNER",
            message=message
        )


class NotOrganizationMember(BaseAPIException):
    """非组织成员"""
    
    def __init__(self, message: str = "您不是该组织的成员"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="NOT_ORGANIZATION_MEMBER",
            message=message
        )


class AlreadyOrganizationMember(BaseAPIException):
    """已是组织成员"""
    
    def __init__(self, message: str = "您已经是该组织的成员"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="ALREADY_ORGANIZATION_MEMBER",
            message=message
        )


class JoinOrganizationLimitExceeded(BaseAPIException):
    """加入组织数量超限"""
    
    def __init__(self, max_count: int, message: Optional[str] = None):
        if message is None:
            message = f"您已达到加入组织的上限（{max_count}个）"
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="JOIN_ORGANIZATION_LIMIT_EXCEEDED",
            message=message,
            details={"max_count": max_count}
        )


# ============ Activation Code Related Exceptions ============

class ActivationCodeInvalid(BaseAPIException):
    """激活码无效"""
    
    def __init__(self, message: str = "激活码不存在或无效"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="ACTIVATION_CODE_INVALID",
            message=message
        )


class ActivationCodeExpired(BaseAPIException):
    """激活码过期"""
    
    def __init__(self, message: str = "激活码已过期"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="ACTIVATION_CODE_EXPIRED",
            message=message
        )


class ActivationCodeUsedUp(BaseAPIException):
    """激活码已用完"""
    
    def __init__(self, message: str = "激活码使用次数已达上限"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="ACTIVATION_CODE_USED_UP",
            message=message
        )


class ActivationCodeAlreadyUsed(BaseAPIException):
    """激活码已被使用"""
    
    def __init__(self, message: str = "您已经使用过此激活码"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="ACTIVATION_CODE_ALREADY_USED",
            message=message
        )


# ============ User Related Exceptions ============

class UsernameAlreadyExists(BaseAPIException):
    """用户名已存在"""
    
    def __init__(self, username: str, message: Optional[str] = None):
        if message is None:
            message = f"用户名 '{username}' 已被使用"
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="USERNAME_ALREADY_EXISTS",
            message=message,
            details={"username": username}
        )


class InvalidUsername(BaseAPIException):
    """用户名格式无效"""
    
    def __init__(self, message: str = "用户名格式不正确，只能包含字母、数字、中文（3-50字符）"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_USERNAME",
            message=message
        )


class PermissionDenied(BaseAPIException):
    """权限不足"""
    
    def __init__(self, message: str = "您没有权限执行此操作"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="PERMISSION_DENIED",
            message=message
        )


class AdminPermissionRequired(BaseAPIException):
    """需要管理员权限"""
    
    def __init__(self, message: str = "此操作需要管理员权限"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="ADMIN_PERMISSION_REQUIRED",
            message=message
        )


class MembershipRequired(BaseAPIException):
    """需要会员权限"""
    
    def __init__(self, message: str = "此功能仅对会员开放"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="MEMBERSHIP_REQUIRED",
            message=message
        )


class MembershipExpired(BaseAPIException):
    """会员已过期"""
    
    def __init__(self, message: str = "您的会员已过期，请续费"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="MEMBERSHIP_EXPIRED",
            message=message
        )


# ============ Knowledge Base Related Exceptions ============

class KnowledgeBaseLimitExceeded(BaseAPIException):
    """知识库数量超限"""
    
    def __init__(self, max_count: int, message: Optional[str] = None):
        if message is None:
            message = f"您已达到知识库数量上限（{max_count}个）"
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="KNOWLEDGE_BASE_LIMIT_EXCEEDED",
            message=message,
            details={"max_count": max_count}
        )


class StorageQuotaExceeded(BaseAPIException):
    """存储配额超限"""
    
    def __init__(self, limit_mb: float, message: Optional[str] = None):
        if message is None:
            message = f"存储空间不足（限制：{limit_mb}MB）"
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="STORAGE_QUOTA_EXCEEDED",
            message=message,
            details={"limit_mb": limit_mb}
        )


class KnowledgeBaseNotFound(BaseAPIException):
    """知识库不存在"""
    
    def __init__(self, kb_id: str, message: Optional[str] = None):
        if message is None:
            message = "知识库不存在"
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="KNOWLEDGE_BASE_NOT_FOUND",
            message=message,
            details={"kb_id": kb_id}
        )


class KnowledgeBaseAccessDenied(BaseAPIException):
    """知识库访问被拒绝"""
    
    def __init__(self, message: str = "您没有权限访问该知识库"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="KNOWLEDGE_BASE_ACCESS_DENIED",
            message=message
        )


class InvalidVisibility(BaseAPIException):
    """无效的可见性设置"""
    
    def __init__(self, message: str = "可见性设置无效"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_VISIBILITY",
            message=message
        )


# ============ File Upload Related Exceptions ============

class FileSizeExceeded(BaseAPIException):
    """文件大小超限"""
    
    def __init__(self, max_size_mb: int, message: Optional[str] = None):
        if message is None:
            message = f"文件大小超过限制（最大：{max_size_mb}MB）"
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="FILE_SIZE_EXCEEDED",
            message=message,
            details={"max_size_mb": max_size_mb}
        )


class InvalidFileType(BaseAPIException):
    """文件类型无效"""
    
    def __init__(self, allowed_types: list, message: Optional[str] = None):
        if message is None:
            message = f"文件类型不支持（支持的类型：{', '.join(allowed_types)}）"
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_FILE_TYPE",
            message=message,
            details={"allowed_types": allowed_types}
        )


# ============ Resource Not Found Exceptions ============

class ResourceNotFound(BaseAPIException):
    """资源不存在"""
    
    def __init__(self, resource_type: str, resource_id: str, message: Optional[str] = None):
        if message is None:
            message = f"{resource_type}不存在"
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="RESOURCE_NOT_FOUND",
            message=message,
            details={"resource_type": resource_type, "resource_id": resource_id}
        )


class UserNotFound(BaseAPIException):
    """用户不存在"""
    
    def __init__(self, user_id: str, message: str = "用户不存在"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="USER_NOT_FOUND",
            message=message,
            details={"user_id": user_id}
        )


class OrganizationNotFound(BaseAPIException):
    """组织不存在"""
    
    def __init__(self, org_id: str, message: str = "组织不存在"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="ORGANIZATION_NOT_FOUND",
            message=message,
            details={"org_id": org_id}
        )


# ============ Validation Exceptions ============

class ValidationError(BaseAPIException):
    """验证错误"""
    
    def __init__(self, field: str, message: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="VALIDATION_ERROR",
            message=message,
            details={"field": field}
        )


class InvalidOperation(BaseAPIException):
    """无效操作"""
    
    def __init__(self, message: str = "操作无效"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_OPERATION",
            message=message
        )


# ============ Token Quota Related Exceptions ============

class QuotaExceeded(BaseAPIException):
    """Token 配额超限"""
    
    def __init__(
        self,
        message: str,
        used_tokens: int,
        quota_limit: int,
        reset_date: str,
        user_level: str
    ):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_code="QUOTA_EXCEEDED",
            message=message,
            details={
                "used_tokens": used_tokens,
                "quota_limit": quota_limit,
                "reset_date": reset_date,
                "user_level": user_level
            }
        )

