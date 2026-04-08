"""Security utilities for password hashing and JWT."""

import bcrypt
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt

from config.settings import settings

# bcrypt has a 72-byte password limit (从配置读取)
MAX_PASSWORD_LENGTH = settings.MAX_PASSWORD_LENGTH
_BCRYPT_ROUNDS = 12


def _truncate_password(password: str) -> bytes:
    """Truncate password bytes to bcrypt's supported length."""
    password_bytes = (password or "").encode("utf-8")
    if len(password_bytes) > MAX_PASSWORD_LENGTH:
        password_bytes = password_bytes[:MAX_PASSWORD_LENGTH]
    return password_bytes


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _truncate_password(plain_password),
            (hashed_password or "").encode("utf-8"),
        )
    except ValueError:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password with bcrypt."""
    hashed = bcrypt.hashpw(
        _truncate_password(password),
        bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
    )
    return hashed.decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify JWT token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None
