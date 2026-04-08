"""Pydantic schemas for the auth domain."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, constr


class LoginRequest(BaseModel):
    """Login request body."""

    email: EmailStr
    password: constr(min_length=6)


class RegisterRequest(BaseModel):
    """Register request body."""

    email: EmailStr
    password: constr(min_length=6)
    name: constr(min_length=1, max_length=50)
    code: constr(min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    """Reset password request body."""

    email: EmailStr
    password: constr(min_length=6)
    code: constr(min_length=6, max_length=6)


class SendVerificationCodeRequest(BaseModel):
    """Send verification code request body."""

    email: EmailStr
    type: constr(pattern="^(register|reset)$") = "register"


class AuthResponse(BaseModel):
    """Auth response with token."""

    token: str
    user: dict


class UserProfile(BaseModel):
    """User profile."""

    id: str
    name: str
    email: str
    avatar: Optional[str] = None
    user_level: Optional[str] = None
    is_member: Optional[bool] = None
    is_advanced_member: Optional[bool] = None
    is_admin: Optional[bool] = None
    member_expires_at: Optional[str] = None
    organizations: Optional[list[dict]] = None


class CheckUsernameRequest(BaseModel):
    """Check username availability request."""

    username: constr(min_length=2, max_length=20)


class CheckUsernameResponse(BaseModel):
    """Check username availability response."""

    available: bool


class UpdateProfileRequest(BaseModel):
    """Update profile request."""

    name: Optional[constr(min_length=2, max_length=20)] = None
    avatar: Optional[str] = None


class UploadAvatarResponse(BaseModel):
    """Upload avatar response."""

    avatar_url: str


class ActivateMembershipRequest(BaseModel):
    """Activate membership request."""

    code: constr(min_length=1, max_length=50)
