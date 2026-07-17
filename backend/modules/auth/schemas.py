"""Pydantic schemas for the auth domain."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, constr, field_validator

from utils.security import validate_password_length


class PasswordRequest(BaseModel):
    """Base request that applies bcrypt's UTF-8 byte limit consistently."""

    password: constr(min_length=6)

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        return validate_password_length(value)


class LoginRequest(PasswordRequest):
    """Login request body."""

    email: EmailStr


class RegisterRequest(PasswordRequest):
    """Register request body."""

    email: EmailStr
    name: constr(min_length=1, max_length=50)
    code: constr(min_length=6, max_length=6)


class ResetPasswordRequest(PasswordRequest):
    """Reset password request body."""

    email: EmailStr
    code: constr(min_length=6, max_length=6)


class SendVerificationCodeRequest(BaseModel):
    """Send verification code request body."""

    email: EmailStr
    type: constr(pattern="^(register|reset)$") = "register"


class AuthResponse(BaseModel):
    """Auth response with token."""

    token: str
    user: dict


class GuestSessionResponse(BaseModel):
    """Server-issued credential for the anonymous chat trial."""

    guest_token: str
    expires_in: int


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
