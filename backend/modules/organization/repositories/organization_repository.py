"""Organization repository for database operations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import joinedload, selectinload
from typing import Optional, List
from datetime import datetime
from modules.organization.entities.organization import Organization
from modules.organization.entities.organization_member import OrganizationMember
import uuid


class OrganizationRepository:
    """Repository for Organization model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(
        self,
        owner_id: uuid.UUID,
        name: str,
        description: Optional[str],
        avatar: Optional[str],
        org_code: str,
        max_members: int
    ) -> Organization:
        """
        Create a new organization.
        
        Args:
            owner_id: Owner user ID
            name: Organization name
            description: Organization description
            avatar: Avatar URL
            org_code: Organization code
            max_members: Maximum members
            
        Returns:
            Created organization
        """
        org = Organization(
            owner_id=owner_id,
            name=name,
            description=description,
            avatar=avatar,
            org_code=org_code,
            max_members=max_members,
            is_deleted=False,
        )
        self.db.add(org)
        await self.db.commit()
        await self.db.refresh(org)
        return org
    
    async def get_by_id(self, org_id: uuid.UUID, include_deleted: bool = False) -> Optional[Organization]:
        """
        Get organization by ID.
        
        Args:
            org_id: Organization ID
            include_deleted: Whether to include soft-deleted organizations
            
        Returns:
            Organization or None
        """
        query = select(Organization).where(Organization.id == org_id)
        
        if not include_deleted:
            query = query.where(Organization.is_deleted == False)
        
        # Load relationships to prevent lazy loading
        # Use selectinload for members to also load nested relationships
        query = query.options(
            selectinload(Organization.members).selectinload(OrganizationMember.user),
            joinedload(Organization.owner)
        )
        
        result = await self.db.execute(query)
        return result.unique().scalar_one_or_none()
    
    async def get_by_code(self, org_code: str) -> Optional[Organization]:
        """Get organization by organization code."""
        result = await self.db.execute(
            select(Organization)
            .where(and_(
                Organization.org_code == org_code,
                Organization.is_deleted == False
            ))
            .options(
                selectinload(Organization.members).selectinload(OrganizationMember.user),
                joinedload(Organization.owner)
            )
        )
        return result.unique().scalar_one_or_none()
    
    async def get_by_owner(self, owner_id: uuid.UUID) -> List[Organization]:
        """Get all organizations owned by a user."""
        result = await self.db.execute(
            select(Organization)
            .where(and_(
                Organization.owner_id == owner_id,
                Organization.is_deleted == False
            ))
            .options(
                selectinload(Organization.members).selectinload(OrganizationMember.user),
                joinedload(Organization.owner)
            )
            .order_by(Organization.created_at.desc())
        )
        return list(result.unique().scalars().all())
    
    async def get_joined_by_user(self, user_id: uuid.UUID) -> List[Organization]:
        """
        Get all organizations a user has joined (as a member, not owner).
        
        Args:
            user_id: User ID
            
        Returns:
            List of organizations
        """
        result = await self.db.execute(
            select(Organization)
            .join(OrganizationMember, Organization.id == OrganizationMember.org_id)
            .where(and_(
                OrganizationMember.user_id == user_id,
                OrganizationMember.role == 'member',
                Organization.is_deleted == False
            ))
            .options(
                selectinload(Organization.members).selectinload(OrganizationMember.user),
                joinedload(Organization.owner)
            )
            .order_by(OrganizationMember.joined_at.desc())
        )
        return list(result.unique().scalars().all())
    
    async def update(self, org_id: uuid.UUID, **kwargs) -> Organization:
        """
        Update organization fields.
        
        Args:
            org_id: Organization ID
            **kwargs: Fields to update
            
        Returns:
            Updated organization
        """
        org = await self.get_by_id(org_id)
        if not org:
            raise ValueError("Organization not found")
        
        for key, value in kwargs.items():
            if hasattr(org, key):
                setattr(org, key, value)
        
        org.updated_at = datetime.now()
        await self.db.commit()
        await self.db.refresh(org)
        return org
    
    async def delete(self, org_id: uuid.UUID) -> bool:
        """
        Soft delete an organization.
        
        Args:
            org_id: Organization ID
            
        Returns:
            True if deleted, False if not found
        """
        org = await self.get_by_id(org_id)
        if not org:
            return False
        
        org.is_deleted = True
        await self.db.commit()
        return True
    
    async def regenerate_code(self, org_id: uuid.UUID, new_code: str) -> Organization:
        """
        Regenerate organization code.
        
        Args:
            org_id: Organization ID
            new_code: New organization code
            
        Returns:
            Updated organization
        """
        org = await self.get_by_id(org_id)
        if not org:
            raise ValueError("Organization not found")
        
        org.org_code = new_code
        org.updated_at = datetime.now()
        await self.db.commit()
        await self.db.refresh(org)
        return org
    
    async def update_code_expiry(self, org_id: uuid.UUID, expires_at: Optional[datetime]) -> Organization:
        """
        Update organization code expiry date.
        
        Args:
            org_id: Organization ID
            expires_at: New expiry date (None for permanent)
            
        Returns:
            Updated organization
        """
        org = await self.get_by_id(org_id)
        if not org:
            raise ValueError("Organization not found")
        
        org.code_expires_at = expires_at
        org.updated_at = datetime.now()
        await self.db.commit()
        await self.db.refresh(org)
        return org
    
    async def count_members(self, org_id: uuid.UUID) -> int:
        """Count the number of members in an organization."""
        result = await self.db.execute(
            select(func.count(OrganizationMember.id))
            .where(OrganizationMember.org_id == org_id)
        )
        return result.scalar() or 0
    
    async def count_all(self) -> int:
        """Count total number of organizations (excluding deleted)."""
        result = await self.db.execute(
            select(func.count(Organization.id))
            .where(Organization.is_deleted == False)
        )
        return result.scalar() or 0
