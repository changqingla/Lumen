"""Authentication API endpoints with membership support."""
import uuid

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from modules.auth.rate_limiter import (
    GUEST_SESSION_RATE_LIMIT,
    LOGIN_RATE_LIMIT,
    REGISTER_RATE_LIMIT,
    RESET_PASSWORD_RATE_LIMIT,
    SEND_CODE_RATE_LIMIT,
    enforce_auth_rate_limit,
)
from modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendVerificationCodeRequest,
    AuthResponse,
    UserProfile,
    CheckUsernameRequest,
    CheckUsernameResponse,
    GuestSessionResponse,
    UpdateProfileRequest,
    UploadAvatarResponse,
    ActivateMembershipRequest,
)
from middlewares.auth import get_current_user
from models.user import User
from config.settings import settings
from utils.security import create_guest_token, decode_guest_token

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _create_auth_service(db: AsyncSession):
    from modules.auth.services.auth_service import AuthService

    return AuthService(db)


@router.post("/guest-session", response_model=GuestSessionResponse)
async def create_guest_session(
    http_request: Request,
    existing_token: str | None = Header(default=None, alias="X-Guest-Token"),
):
    """Issue or reuse an unforgeable identity for the anonymous chat trial."""
    token = str(existing_token or "").strip()
    if token and decode_guest_token(token) is not None:
        return {
            "guest_token": token,
            "expires_in": settings.GUEST_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        }

    # Expired or otherwise invalid local credentials follow the same
    # rate-limited issuance path as a first visit, so clients can recover.
    await enforce_auth_rate_limit(http_request, GUEST_SESSION_RATE_LIMIT)
    token = create_guest_token(str(uuid.uuid4()))
    return {
        "guest_token": token,
        "expires_in": settings.GUEST_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    }


@router.post("/login", response_model=AuthResponse)
async def login(
    http_request: Request,
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """User login endpoint."""
    await enforce_auth_rate_limit(http_request, LOGIN_RATE_LIMIT, request.email)
    service = _create_auth_service(db)
    token, user_data = await service.login(request.email, request.password)
    return {"token": token, "user": user_data}


@router.post("/send-code")
async def send_code(
    http_request: Request,
    request: SendVerificationCodeRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Send verification code to email.
    type: 'register' or 'reset'
    """
    await enforce_auth_rate_limit(
        http_request,
        SEND_CODE_RATE_LIMIT,
        f"{request.type}:{request.email}",
    )
    service = _create_auth_service(db)
    success = await service.send_verification_code(request.email, request.type)
    if not success:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": "INTERNAL_ERROR", "message": "邮件发送失败"}}
        )
    return {"message": "如果该邮箱可用于当前操作，验证码已发送"}


@router.post("/register", response_model=AuthResponse)
async def register(
    http_request: Request,
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """User registration endpoint."""
    await enforce_auth_rate_limit(http_request, REGISTER_RATE_LIMIT, request.email)
    service = _create_auth_service(db)
    token, user_data = await service.register(
        request.email, 
        request.password, 
        request.name,
        request.code
    )
    return {"token": token, "user": user_data}


@router.post("/reset-password")
async def reset_password(
    http_request: Request,
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    """Reset user password endpoint."""
    await enforce_auth_rate_limit(http_request, RESET_PASSWORD_RATE_LIMIT, request.email)
    service = _create_auth_service(db)
    await service.reset_password(
        request.email,
        request.password,
        request.code
    )
    return {"message": "密码重置成功"}


@router.get("/me", response_model=UserProfile)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current user profile with organizations."""
    service = _create_auth_service(db)
    user_data = await service.get_user_with_organizations(current_user.id)
    return user_data


@router.post("/check-username", response_model=CheckUsernameResponse)
async def check_username(
    request: CheckUsernameRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check if username is available."""
    service = _create_auth_service(db)
    available = await service.check_username_available(request.username, current_user.id)
    return {"available": available}


@router.patch("/profile", response_model=UserProfile)
async def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update user profile."""
    service = _create_auth_service(db)
    user_data = await service.update_profile(
        current_user.id,
        name=request.name,
        avatar=request.avatar
    )
    return user_data


@router.post("/upload-avatar", response_model=UploadAvatarResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload user avatar."""
    service = _create_auth_service(db)
    result = await service.upload_avatar(current_user.id, file)
    return result


@router.post("/activate", response_model=UserProfile)
async def activate_membership(
    request: ActivateMembershipRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Activate membership with activation code."""
    service = _create_auth_service(db)
    user_data = await service.activate_membership(current_user.id, request.code)
    return user_data
