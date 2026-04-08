"""Pydantic schemas for the organization domain."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, constr


class CreateOrganizationRequest(BaseModel):
    """Create organization request."""

    name: constr(min_length=3, max_length=100)
    description: Optional[str] = None
    avatar: Optional[str] = None


class UpdateOrganizationRequest(BaseModel):
    """Update organization request."""

    name: Optional[constr(min_length=3, max_length=100)] = None
    description: Optional[str] = None
    avatar: Optional[str] = None


class JoinOrganizationRequest(BaseModel):
    """Join organization request."""

    org_code: constr(min_length=1, max_length=20)


class OrganizationResponse(BaseModel):
    """Organization response."""

    id: str
    name: str
    description: Optional[str] = None
    avatar: Optional[str] = None
    org_code: str
    code_expires_at: Optional[str] = None
    owner_id: str
    owner_name: Optional[str] = None
    max_members: int
    member_count: int
    created_at: str
    updated_at: str
    role: Optional[str] = None
    is_owner: Optional[bool] = None


class OrganizationListResponse(BaseModel):
    """Organization list response with created and joined groups."""

    created: list[OrganizationResponse]
    joined: list[OrganizationResponse]


class OrganizationMemberResponse(BaseModel):
    """Organization member response."""

    id: str
    user_id: str
    user_name: str
    user_email: str
    user_avatar: Optional[str] = None
    role: str
    joined_at: str


class OrganizationDetailResponse(OrganizationResponse):
    """Organization detail response including members."""

    members: list[OrganizationMemberResponse]


class RegenerateCodeResponse(BaseModel):
    """Regenerate organization code response."""

    org_code: str


class SetCodeExpiryRequest(BaseModel):
    """Set organization code expiry request."""

    expires_at: Optional[datetime] = None
