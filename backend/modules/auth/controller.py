"""Authentication API endpoints with membership support."""
from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import get_db
from modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendVerificationCodeRequest,
    AuthResponse,
    UserProfile,
    CheckUsernameRequest,
    CheckUsernameResponse,
    UpdateProfileRequest,
    UploadAvatarResponse,
    ActivateMembershipRequest,
)
from middlewares.auth import get_current_user
from models.user import User
import uuid

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _create_auth_service(db: AsyncSession):
    from modules.auth.services.auth_service import AuthService

    return AuthService(db)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """User login endpoint."""
    service = _create_auth_service(db)
    token, user_data = await service.login(request.email, request.password)
    return {"token": token, "user": user_data}


@router.post("/send-code")
async def send_code(
    request: SendVerificationCodeRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Send verification code to email.
    type: 'register' or 'reset'
    """
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
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """User registration endpoint."""
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
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    """Reset user password endpoint."""
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
