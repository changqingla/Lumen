"""Authentication middleware and dependencies."""
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import get_db

if TYPE_CHECKING:
    from models.user import User

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)
GUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")


@dataclass(slots=True)
class AuthenticatedIdentity:
    user: "User"
    is_guest: bool = False
    guest_id: str | None = None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """Get current authenticated user from JWT token."""
    from repositories.user_repository import UserRepository
    from utils.security import decode_access_token

    token = credentials.credentials
    payload = decode_access_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Invalid or expired token"}}
        )
    
    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Invalid token payload"}}
        )
    
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "User not found"}}
        )
    
    return user


def is_guest_user(user: Any) -> bool:
    return str(getattr(user, "email", "") or "").lower().endswith("@guest.lumen.local")


async def get_current_chat_identity(
    guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatedIdentity:
    """Resolve a signed-in user or a guest chat identity for chat-only endpoints."""
    if credentials is not None:
        user = await get_current_user(credentials, db)
        return AuthenticatedIdentity(user=user, is_guest=False, guest_id=None)

    normalized_guest_id = str(guest_id or "").strip()
    if not normalized_guest_id or not GUEST_ID_PATTERN.match(normalized_guest_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Missing authentication"}},
        )

    from repositories.user_repository import UserRepository

    user_repo = UserRepository(db)
    user = await user_repo.get_or_create_guest_user(normalized_guest_id)
    return AuthenticatedIdentity(user=user, is_guest=True, guest_id=normalized_guest_id)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: AsyncSession = Depends(get_db)
):
    """Get current user if authenticated, None otherwise."""
    if credentials is None:
        return None
    
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None
