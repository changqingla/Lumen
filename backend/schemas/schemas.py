"""Pydantic schemas for request/response validation."""
from pydantic import BaseModel, Field, constr
from typing import Optional, List
from enum import Enum


# === Common ===
class ErrorCode(str, Enum):
    """Standard error codes."""
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: dict = Field(
        ...,
        json_schema_extra={
            "example": {
                "code": "VALIDATION_ERROR",
                "message": "参数不合法",
                "details": {},
            }
        },
    )


class PaginationMeta(BaseModel):
    """Pagination metadata."""
    total: int
    page: int
    pageSize: int


# === Favorites ===
class FavoriteType(str, Enum):
    """Favorite item type."""
    PAPER = "paper"
    KNOWLEDGE = "knowledge"


class CreateFavoriteRequest(BaseModel):
    """Create favorite request."""
    type: FavoriteType
    targetId: str
    tags: List[str] = []


class FavoriteItem(BaseModel):
    """Favorite item response."""
    id: str
    type: FavoriteType
    title: str
    description: Optional[str] = None
    author: Optional[str] = None
    date: str
    source: Optional[str] = None
    tags: List[str] = []


class FavoritesResponse(BaseModel):
    """Favorites list response."""
    total: int
    page: int
    pageSize: int
    items: List[FavoriteItem]


# === Knowledge Base (TODO) ===
class CreateKnowledgeBaseRequest(BaseModel):
    """Create knowledge base request."""
    name: str
    description: Optional[str] = ""
    tags: List[str] = []


# === Hub (TODO) ===
class HubItem(BaseModel):
    """Hub item for knowledge plaza."""
    id: str
    title: str
    desc: str
    icon: str
    subs: int
    contents: int


# === Activation Code (Admin) ===
class GenerateActivationCodeRequest(BaseModel):
    """Generate activation code request."""
    type: constr(pattern="^(member|premium)$")  # member or premium
    duration_days: Optional[int] = None  # None for permanent
    max_usage: int = Field(default=1, ge=1, le=1000)
    code_expires_in_days: Optional[int] = None  # Code expiry


class ActivationCodeResponse(BaseModel):
    """Activation code response."""
    id: str
    code: str
    type: str
    duration_days: Optional[int] = None
    max_usage: int
    used_count: int
    created_by: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None
    is_active: bool
    is_valid: bool


class ValidateCodeResponse(BaseModel):
    """Validate activation code response."""
    valid: bool
    type: Optional[str] = None
    duration_days: Optional[int] = None
    remaining_usage: Optional[int] = None
    reason: Optional[str] = None


# === Knowledge Base Visibility ===
class UpdateKBVisibilityRequest(BaseModel):
    """Update knowledge base visibility request."""
    visibility: constr(pattern="^(private|organization|public)$")
    shared_to_orgs: Optional[List[str]] = None  # Organization IDs when visibility is 'organization'


class ShareToOrgsRequest(BaseModel):
    """Share knowledge base to organizations request."""
    org_ids: List[str]
