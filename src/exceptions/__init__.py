"""Custom exceptions package."""

from .custom_exceptions import (
    BaseAPIException,
    # Organization exceptions
    OrganizationLimitExceeded,
    OrganizationMemberLimitExceeded,
    OrgCodeExpired,
    OrgCodeInvalid,
    NotOrganizationOwner,
    NotOrganizationMember,
    AlreadyOrganizationMember,
    JoinOrganizationLimitExceeded,
    # Activation code exceptions
    ActivationCodeInvalid,
    ActivationCodeExpired,
    ActivationCodeUsedUp,
    ActivationCodeAlreadyUsed,
    # User exceptions
    UsernameAlreadyExists,
    InvalidUsername,
    PermissionDenied,
    AdminPermissionRequired,
    MembershipRequired,
    MembershipExpired,
    # Knowledge base exceptions
    KnowledgeBaseLimitExceeded,
    StorageQuotaExceeded,
    KnowledgeBaseNotFound,
    KnowledgeBaseAccessDenied,
    InvalidVisibility,
    # File upload exceptions
    FileSizeExceeded,
    InvalidFileType,
    # Resource not found exceptions
    ResourceNotFound,
    UserNotFound,
    OrganizationNotFound,
    # Validation exceptions
    ValidationError,
    InvalidOperation,
    # Token quota exceptions
    QuotaExceeded,
)

__all__ = [
    "BaseAPIException",
    "OrganizationLimitExceeded",
    "OrganizationMemberLimitExceeded",
    "OrgCodeExpired",
    "OrgCodeInvalid",
    "NotOrganizationOwner",
    "NotOrganizationMember",
    "AlreadyOrganizationMember",
    "JoinOrganizationLimitExceeded",
    "ActivationCodeInvalid",
    "ActivationCodeExpired",
    "ActivationCodeUsedUp",
    "ActivationCodeAlreadyUsed",
    "UsernameAlreadyExists",
    "InvalidUsername",
    "PermissionDenied",
    "AdminPermissionRequired",
    "MembershipRequired",
    "MembershipExpired",
    "KnowledgeBaseLimitExceeded",
    "StorageQuotaExceeded",
    "KnowledgeBaseNotFound",
    "KnowledgeBaseAccessDenied",
    "InvalidVisibility",
    "FileSizeExceeded",
    "InvalidFileType",
    "ResourceNotFound",
    "UserNotFound",
    "OrganizationNotFound",
    "ValidationError",
    "InvalidOperation",
    "QuotaExceeded",
]

