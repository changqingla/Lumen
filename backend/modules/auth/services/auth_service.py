"""Authentication service business logic with membership support."""
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status, UploadFile
from repositories.user_repository import UserRepository
from modules.admin.repositories.activation_code_repository import ActivationCodeRepository
from modules.notes.repositories.note_repository import NoteFolderRepository
from modules.knowledge.repositories.kb_repository import KnowledgeBaseRepository
from modules.organization.repositories.organization_member_repository import OrganizationMemberRepository
from infrastructure.services.email_service import EmailService
from utils.security import verify_password, get_password_hash, create_access_token
from utils.minio_client import upload_file
from config.settings import settings
from typing import Tuple, Optional, Dict
from datetime import datetime
import uuid
import hashlib


class AuthService:
    """Service for authentication operations with membership support."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.user_repo = UserRepository(db)
        self.email_service = EmailService()

    @staticmethod
    def _invalid_credentials_error() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "邮箱或密码错误"}},
        )

    async def send_verification_code(self, email: str, type: str = "register") -> bool:
        """
        Send verification code to email.
        
        Args:
            email: Email address
            type: 'register' or 'reset'
        """
        existing = await self.user_repo.get_by_email(email)
        
        if type == "register" and existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "CONFLICT", "message": "Email already registered"}}
            )
        if type == "reset" and not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "账号未注册"}}
            )

        return await self.email_service.send_verification_code(email)

    async def login(self, email: str, password: str) -> Tuple[str, dict]:
        """Login user and return token."""
        user = await self.user_repo.get_by_email(email)

        if not user:
            raise self._invalid_credentials_error()

        if not verify_password(password, user.password_hash):
            raise self._invalid_credentials_error()

        # Create access token
        access_token = create_access_token(data={"sub": str(user.id)})

        return access_token, user.to_dict()
    
    async def register(self, email: str, password: str, name: str, code: str) -> Tuple[str, dict]:
        """Register new user."""
        # Check verification code first
        is_valid = await self.email_service.verify_code(email, code)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_CODE", "message": "验证码无效或已过期"}}
            )

        # Check if user already exists
        existing = await self.user_repo.get_by_email(email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "CONFLICT", "message": "Email already registered"}}
            )
        
        # Check username length (max 8 characters)
        if len(name) > 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "NAME_TOO_LONG", "message": "用户名不能超过8个字符"}}
            )
        
        # Check username uniqueness
        is_available = await self.user_repo.check_username_available(name)
        if not is_available:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "NAME_CONFLICT", "message": "用户名已被占用"}}
            )
        
        # Hash password
        password_hash = get_password_hash(password)
        
        # Create user
        user = await self.user_repo.create(email, password_hash, name)
        
        # Create default folders for new user
        folder_repo = NoteFolderRepository(self.db)
        for folder_name in settings.DEFAULT_USER_FOLDERS:
            await folder_repo.create(str(user.id), folder_name)
        
        # Create default knowledge base for new user
        kb_repo = KnowledgeBaseRepository(self.db)
        await kb_repo.create(
            owner_id=str(user.id),
            name=settings.DEFAULT_KB_NAME,
            description=settings.DEFAULT_KB_DESCRIPTION,
            category=settings.DEFAULT_KB_CATEGORY
        )
        
        # Create access token
        access_token = create_access_token(data={"sub": str(user.id)})
        
        return access_token, user.to_dict()
    
    async def reset_password(self, email: str, password: str, code: str) -> None:
        """Reset user password."""
        # Check verification code first
        is_valid = await self.email_service.verify_code(email, code)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_CODE", "message": "验证码无效或已过期"}}
            )
        
        user = await self.user_repo.get_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "账号未注册"}}
            )
        
        # Hash new password
        password_hash = get_password_hash(password)
        
        # Update user password
        await self.user_repo.update_password(user.id, password_hash)
    
    async def get_user_with_organizations(self, user_id: uuid.UUID) -> Dict:
        """
        Get user info with organizations.
        
        Args:
            user_id: User ID
            
        Returns:
            User dict with organizations
        """
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "User not found"}}
            )
        
        # Get user's organizations
        org_member_repo = OrganizationMemberRepository(self.db)
        memberships = await org_member_repo.get_user_organizations(user_id)
        
        user_dict = user.to_dict()
        user_dict['organizations'] = [
            {
                'id': str(m.org_id),
                'name': m.organization.name if m.organization else None,
                'role': m.role,
            }
            for m in memberships
        ]
        
        return user_dict
    
    async def check_username_available(self, username: str, exclude_user_id: Optional[uuid.UUID] = None) -> bool:
        """
        Check if username is available.
        
        Args:
            username: Username to check
            exclude_user_id: User ID to exclude from check (for profile update)
            
        Returns:
            True if available, False otherwise
        """
        return await self.user_repo.check_username_available(username, exclude_user_id)
    
    async def update_profile(
        self,
        user_id: uuid.UUID,
        name: Optional[str] = None,
        avatar: Optional[str] = None
    ) -> Dict:
        """
        Update user profile.
        
        Args:
            user_id: User ID
            name: New name (optional)
            avatar: New avatar URL (optional)
            
        Returns:
            Updated user dict
        """
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "User not found"}}
            )
        
        # Check username length and uniqueness if changing name
        if name and name != user.name:
            # Check length (max 8 characters)
            if len(name) > 8:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": "NAME_TOO_LONG", "message": "用户名不能超过8个字符"}}
                )
            # Check uniqueness
            is_available = await self.user_repo.check_username_available(name, user_id)
            if not is_available:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"error": {"code": "NAME_CONFLICT", "message": "用户名已被占用"}}
                )
        
        # Update profile
        updated_user = await self.user_repo.update_profile(user_id, name, avatar)
        return updated_user.to_dict()
    
    async def upload_avatar(self, user_id: uuid.UUID, file: UploadFile) -> Dict:
        """
        Upload user avatar.
        
        Args:
            user_id: User ID
            file: Uploaded file
            
        Returns:
            Dict with avatar_url
        """
        # Validate file type
        allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/svg+xml']
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_FILE_TYPE", "message": "仅支持 JPG, PNG, WEBP, SVG 格式"}}
            )
        
        # Read file data
        file_data = await file.read()
        
        # Validate file size (从配置读取)
        max_size = settings.MAX_AVATAR_SIZE
        if len(file_data) > max_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "FILE_TOO_LARGE", "message": f"文件大小不能超过{max_size // 1024 // 1024}MB"}}
            )
        
        # Generate unique filename
        file_extension = file.filename.split('.')[-1] if '.' in file.filename else 'jpg'
        file_hash = hashlib.md5(file_data).hexdigest()
        object_name = f"avatars/{user_id}/{file_hash}.{file_extension}"
        
        # Upload to MinIO
        try:
            await upload_file(object_name, file_data, file.content_type)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": "UPLOAD_FAILED", "message": f"上传失败: {str(e)}"}}
            )
        
        # Generate public URL
        # Use Nginx proxy path: /minio/{bucket}/{object_name}
        avatar_url = f"/minio/{settings.MINIO_BUCKET}/{object_name}"
        
        # Update user avatar
        await self.user_repo.update_profile(user_id, avatar=avatar_url)
        
        return {"avatar_url": avatar_url}
    
    async def activate_membership(self, user_id: uuid.UUID, code: str) -> Dict:
        """
        Activate membership with activation code.
        
        Args:
            user_id: User ID
            code: Activation code
            
        Returns:
            Updated user dict
        """
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "User not found"}}
            )
        
        # Get and validate activation code
        activation_code_repo = ActivationCodeRepository(self.db)
        activation_code = await activation_code_repo.get_by_code(code)
        
        if not activation_code:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "INVALID_CODE", "message": "激活码不存在"}}
            )
        
        # Check if code can be used
        can_use, reason = activation_code.can_use()
        if not can_use:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_CODE", "message": reason}}
            )
        
        # Check if user already used this code
        if user.activated_codes and code in user.activated_codes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "ALREADY_USED", "message": "您已经使用过此激活码"}}
            )
        
        # Calculate new membership expiry
        new_expiry = activation_code.get_membership_expiry_date(user.membership_expires_at)
        
        # Update user level
        new_level = activation_code.type  # 'member' or 'premium'
        updated_user = await self.user_repo.update_user_level(
            user_id,
            new_level,
            new_expiry,
            code
        )
        
        # Increment activation code usage
        await activation_code_repo.increment_usage(activation_code.id)
        
        return updated_user.to_dict()
