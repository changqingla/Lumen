"""Security utilities for password hashing and JWT."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from jwt import InvalidTokenError

from config.settings import settings

# bcrypt has a 72-byte password limit (从配置读取)
MAX_PASSWORD_LENGTH = settings.MAX_PASSWORD_LENGTH
_BCRYPT_ROUNDS = 12
_ACCESS_TOKEN_TYPE = "access"
_GUEST_TOKEN_TYPE = "guest"


def _password_bytes(password: str) -> bytes:
    """Encode a password and reject values bcrypt cannot represent safely."""
    password_bytes = (password or "").encode("utf-8")
    if len(password_bytes) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must not exceed {MAX_PASSWORD_LENGTH} UTF-8 bytes")
    return password_bytes


def validate_password_length(password: str) -> str:
    """Validate bcrypt's byte limit while preserving the original string."""
    _password_bytes(password)
    return password


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _password_bytes(plain_password),
            (hashed_password or "").encode("utf-8"),
        )
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password with bcrypt."""
    hashed = bcrypt.hashpw(
        _password_bytes(password),
        bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
    )
    return hashed.decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    if data.get("purpose") is not None:
        raise ValueError("Access tokens cannot carry a resource purpose")
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "token_type": _ACCESS_TOKEN_TYPE})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode only a login access token, never another signed token class."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if (
            payload.get("token_type") != _ACCESS_TOKEN_TYPE
            or payload.get("purpose") is not None
        ):
            return None
        return payload
    except InvalidTokenError:
        return None


def create_guest_token(guest_id: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed credential for a server-issued guest identity."""
    normalized_guest_id = str(uuid.UUID(str(guest_id)))
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=settings.GUEST_TOKEN_EXPIRE_DAYS))
    return jwt.encode(
        {
            "sub": normalized_guest_id,
            "guest_id": normalized_guest_id,
            "token_type": _GUEST_TOKEN_TYPE,
            "iat": now,
            "exp": expire,
            "jti": str(uuid.uuid4()),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_guest_token(token: str) -> Optional[dict]:
    """Decode a guest credential and reject access or malformed tokens."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("token_type") != _GUEST_TOKEN_TYPE:
            return None
        guest_id = str(uuid.UUID(str(payload.get("guest_id") or "")))
        if payload.get("sub") != guest_id:
            return None
        payload["guest_id"] = guest_id
        return payload
    except (InvalidTokenError, TypeError, ValueError, AttributeError):
        return None
