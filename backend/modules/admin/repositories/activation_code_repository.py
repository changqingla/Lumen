"""Activation code repository for database operations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional, List
from datetime import datetime
from modules.admin.entities.activation_code import ActivationCode
import uuid


class ActivationCodeRepository:
    """Repository for ActivationCode model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def _build_filtered_query(
        self,
        type: Optional[str] = None,
        is_active: Optional[bool] = None,
    ):
        """Build a base query with shared activation-code filters."""
        query = select(ActivationCode)

        filters = []
        if type:
            filters.append(ActivationCode.type == type)
        if is_active is not None:
            filters.append(ActivationCode.is_active == is_active)

        if filters:
            query = query.where(and_(*filters))

        return query

    async def create(
        self,
        code: str,
        type: str,
        duration_days: Optional[int],
        max_usage: int,
        created_by: Optional[uuid.UUID],
        expires_at: Optional[datetime] = None
    ) -> ActivationCode:
        """
        Create a new activation code.
        
        Args:
            code: Activation code string
            type: Code type (member/premium)
            duration_days: Membership duration in days (None for permanent)
            max_usage: Maximum usage count
            created_by: Creator user ID
            expires_at: Code expiry date
            
        Returns:
            Created activation code
        """
        activation_code = ActivationCode(
            code=code,
            type=type,
            duration_days=duration_days,
            max_usage=max_usage,
            created_by=created_by,
            expires_at=expires_at,
            is_active=True,
            used_count=0,
        )
        self.db.add(activation_code)
        await self.db.commit()
        await self.db.refresh(activation_code)
        return activation_code

    async def get_by_code(self, code: str) -> Optional[ActivationCode]:
        """Get activation code by code string."""
        result = await self.db.execute(
            select(ActivationCode).where(ActivationCode.code == code)
        )
        return result.scalar_one_or_none()
    
    async def get_by_id(self, code_id: uuid.UUID) -> Optional[ActivationCode]:
        """Get activation code by ID."""
        result = await self.db.execute(
            select(ActivationCode).where(ActivationCode.id == code_id)
        )
        return result.scalar_one_or_none()
    
    async def list_codes(
        self,
        type: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[ActivationCode]:
        """
        List activation codes with filters.
        
        Args:
            type: Filter by type (member/premium)
            is_active: Filter by active status
            limit: Maximum number of codes to return
            offset: Offset for pagination
            
        Returns:
            List of activation codes
        """
        query = self._build_filtered_query(type=type, is_active=is_active)

        # Order by created_at descending
        query = query.order_by(ActivationCode.created_at.desc())
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def count_codes(
        self,
        type: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> int:
        """Count activation codes with the same filters used by list queries."""
        query = self._build_filtered_query(type=type, is_active=is_active)
        count_query = query.with_only_columns(func.count(ActivationCode.id)).order_by(None)
        result = await self.db.execute(count_query)
        return result.scalar() or 0
    
    async def deactivate(self, code: str) -> bool:
        """
        Deactivate an activation code.
        
        Args:
            code: Activation code string
            
        Returns:
            True if deactivated, False if not found
        """
        activation_code = await self.get_by_code(code)
        if not activation_code:
            return False
        
        activation_code.is_active = False
        await self.db.commit()
        return True
    
    async def increment_usage(self, code_id: uuid.UUID) -> ActivationCode:
        """
        Increment the usage count of an activation code.
        
        Args:
            code_id: Activation code ID
            
        Returns:
            Updated activation code
        """
        activation_code = await self.get_by_id(code_id)
        if not activation_code:
            raise ValueError("Activation code not found")
        
        activation_code.used_count += 1
        await self.db.commit()
        await self.db.refresh(activation_code)
        return activation_code
