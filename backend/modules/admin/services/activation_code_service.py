"""Activation code service business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from modules.admin.repositories.activation_code_repository import ActivationCodeRepository
from repositories.user_repository import UserRepository
from modules.admin.entities.activation_code import ActivationCode
from typing import Dict, Optional
from datetime import datetime, timedelta, timezone
import uuid


class ActivationCodeService:
    """Service for activation code operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.activation_code_repo = ActivationCodeRepository(db)
        self.user_repo = UserRepository(db)
    
    async def generate_code(
        self,
        admin_id: uuid.UUID,
        type: str,
        duration_days: Optional[int],
        max_usage: int = 1,
        code_expires_in_days: Optional[int] = None
    ) -> Dict:
        """
        Generate a new activation code.
        
        Args:
            admin_id: Admin user ID
            type: Code type (member/premium)
            duration_days: Membership duration (None for permanent)
            max_usage: Maximum usage count
            code_expires_in_days: Code expiry in days (None for permanent)
            
        Returns:
            Created activation code dict
        """
        # Check admin permission
        admin = await self.user_repo.get_by_id(admin_id)
        if not admin or not admin.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅管理员可以生成激活码"}}
            )
        
        # Validate type
        if type not in ('member', 'premium'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_TYPE", "message": "激活码类型必须是 member 或 premium"}}
            )
        
        # Generate unique code
        code = ActivationCode.generate_code(12)
        
        # Calculate expiry date
        expires_at = None
        if code_expires_in_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=code_expires_in_days)
        
        # Create activation code
        activation_code = await self.activation_code_repo.create(
            code=code,
            type=type,
            duration_days=duration_days,
            max_usage=max_usage,
            created_by=admin_id,
            expires_at=expires_at
        )
        
        return activation_code.to_dict()
    
    async def list_codes(
        self,
        admin_id: uuid.UUID,
        type: Optional[str] = None,
        is_active: Optional[bool] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict:
        """
        List activation codes.
        
        Args:
            admin_id: Admin user ID
            type: Filter by type
            is_active: Filter by active status
            page: Page number
            page_size: Page size
            
        Returns:
            Dict with items and total
        """
        # Check admin permission
        admin = await self.user_repo.get_by_id(admin_id)
        if not admin or not admin.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅管理员可以查看激活码列表"}}
            )
        
        offset = (page - 1) * page_size
        total = await self.activation_code_repo.count_codes(type=type, is_active=is_active)
        codes = await self.activation_code_repo.list_codes(
            type=type,
            is_active=is_active,
            limit=page_size,
            offset=offset
        )
        
        return {
            "items": [code.to_dict() for code in codes],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    
    async def deactivate_code(self, admin_id: uuid.UUID, code: str) -> Dict:
        """
        Deactivate an activation code.
        
        Args:
            admin_id: Admin user ID
            code: Activation code to deactivate
            
        Returns:
            Success message
        """
        # Check admin permission
        admin = await self.user_repo.get_by_id(admin_id)
        if not admin or not admin.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅管理员可以作废激活码"}}
            )
        
        success = await self.activation_code_repo.deactivate(code)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "激活码不存在"}}
            )
        
        return {"message": "激活码已作废"}
    
    async def validate_code(self, code: str) -> Dict:
        """
        Validate an activation code (public endpoint).
        
        Args:
            code: Activation code to validate
            
        Returns:
            Validation result
        """
        activation_code = await self.activation_code_repo.get_by_code(code)
        
        if not activation_code:
            return {
                "valid": False,
                "reason": "激活码不存在"
            }
        
        can_use, reason = activation_code.can_use()
        
        if not can_use:
            return {
                "valid": False,
                "reason": reason
            }
        
        return {
            "valid": True,
            "type": activation_code.type,
            "duration_days": activation_code.duration_days,
            "remaining_usage": activation_code.max_usage - activation_code.used_count
        }
    
    async def batch_generate_codes(
        self,
        admin_id: uuid.UUID,
        count: int,
        type: str,
        duration_days: Optional[int],
        max_usage: int = 1,
        code_expires_in_days: Optional[int] = None
    ) -> Dict:
        """
        Batch generate activation codes.
        
        Args:
            admin_id: Admin user ID
            count: Number of codes to generate (1-100)
            type: Code type (member/premium)
            duration_days: Membership duration (None for permanent)
            max_usage: Maximum usage count per code
            code_expires_in_days: Code expiry in days (None for permanent)
            
        Returns:
            Dict with generated codes list
        """
        # Check admin permission
        admin = await self.user_repo.get_by_id(admin_id)
        if not admin or not admin.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅管理员可以生成激活码"}}
            )
        
        # Validate count
        if count < 1 or count > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_COUNT", "message": "批量生成数量必须在1-100之间"}}
            )
        
        # Validate type
        if type not in ('member', 'premium'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "INVALID_TYPE", "message": "激活码类型必须是 member 或 premium"}}
            )
        
        # Calculate expiry date
        expires_at = None
        if code_expires_in_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=code_expires_in_days)
        
        # Generate codes
        generated_codes = []
        for _ in range(count):
            code = ActivationCode.generate_code(12)
            
            # Create activation code
            activation_code = await self.activation_code_repo.create(
                code=code,
                type=type,
                duration_days=duration_days,
                max_usage=max_usage,
                created_by=admin_id,
                expires_at=expires_at
            )
            
            generated_codes.append(activation_code.to_dict())
        
        return {
            "count": count,
            "type": type,
            "duration_days": duration_days,
            "max_usage": max_usage,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "codes": generated_codes
        }
