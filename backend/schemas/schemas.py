"""Pydantic schemas for request/response validation."""
from typing import Any, Optional, List

from pydantic import BaseModel, Field, constr


# === Knowledge Base ===
class CreateKnowledgeBaseRequest(BaseModel):
    """Create knowledge base request."""
    name: str
    description: Optional[str] = ""
    category: str = "其它"
    tags: List[str] = []


class UpdateKnowledgeBaseRequest(BaseModel):
    """Update knowledge base request."""
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    avatar: Optional[str] = None


class InitDirectUploadRequest(BaseModel):
    """Initialize direct upload request."""
    filename: str
    size: int = 0
    contentType: Optional[str] = None


class CompleteDirectUploadRequest(BaseModel):
    """Complete direct upload request."""
    docId: str


class BatchDocumentMarkdownRequest(BaseModel):
    """Batch document markdown request."""
    docIds: List[str]


class MoveDocumentRequest(BaseModel):
    """Move document request."""
    targetKbId: str


class FavoriteCheckRequest(BaseModel):
    """Batch favorite check request."""
    items: List[dict[str, Any]] = []


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
