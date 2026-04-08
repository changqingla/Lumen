"""Organization member repository for database operations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, delete
from sqlalchemy.orm import joinedload
from typing import Optional, List
from modules.organization.entities.organization_member import OrganizationMember
from modules.organization.entities.organization import Organization
import uuid


class OrganizationMemberRepository:
    """Repository for OrganizationMember model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def add_member(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str = 'member'
    ) -> OrganizationMember:
        """
        Add a member to an organization.
        
        Args:
            org_id: Organization ID
            user_id: User ID
            role: Member role (owner/member)
            
        Returns:
            Created organization member
        """
        member = OrganizationMember(
            org_id=org_id,
            user_id=user_id,
            role=role,
        )
        self.db.add(member)
        await self.db.commit()
        await self.db.refresh(member)
        return member
    
    async def remove_member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """
        Remove a member from an organization.
        
        Args:
            org_id: Organization ID
            user_id: User ID
            
        Returns:
            True if removed, False if not found
        """
        result = await self.db.execute(
            delete(OrganizationMember)
            .where(and_(
                OrganizationMember.org_id == org_id,
                OrganizationMember.user_id == user_id
            ))
        )
        await self.db.commit()
        return result.rowcount > 0
    
    async def get_member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Optional[OrganizationMember]:
        """
        Get a specific member record.
        
        Args:
            org_id: Organization ID
            user_id: User ID
            
        Returns:
            Organization member or None
        """
        result = await self.db.execute(
            select(OrganizationMember)
            .where(and_(
                OrganizationMember.org_id == org_id,
                OrganizationMember.user_id == user_id
            ))
            .options(
                joinedload(OrganizationMember.organization),
                joinedload(OrganizationMember.user)
            )
        )
        return result.unique().scalar_one_or_none()
    
    async def get_members(self, org_id: uuid.UUID) -> List[OrganizationMember]:
        """
        Get all members of an organization.
        
        Args:
            org_id: Organization ID
            
        Returns:
            List of organization members
        """
        result = await self.db.execute(
            select(OrganizationMember)
            .where(OrganizationMember.org_id == org_id)
            .options(joinedload(OrganizationMember.user))
            .order_by(OrganizationMember.joined_at.asc())
        )
        return list(result.unique().scalars().all())
    
    async def is_member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """
        Check if a user is a member of an organization (excluding deleted organizations).
        
        Args:
            org_id: Organization ID
            user_id: User ID
            
        Returns:
            True if member, False otherwise
        """
        result = await self.db.execute(
            select(func.count(OrganizationMember.id))
            .join(Organization, OrganizationMember.org_id == Organization.id)
            .where(and_(
                OrganizationMember.org_id == org_id,
                OrganizationMember.user_id == user_id,
                Organization.is_deleted == False
            ))
        )
        count = result.scalar() or 0
        return count > 0
    
    async def count_user_organizations(self, user_id: uuid.UUID, role: Optional[str] = None) -> int:
        """
        Count number of organizations a user is in (excluding deleted organizations).
        
        Args:
            user_id: User ID
            role: Filter by role (optional)
            
        Returns:
            Number of organizations
        """
        query = (
            select(func.count(OrganizationMember.id))
            .join(Organization, OrganizationMember.org_id == Organization.id)
            .where(
                OrganizationMember.user_id == user_id,
                Organization.is_deleted == False
            )
        )
        
        if role:
            query = query.where(OrganizationMember.role == role)
        
        result = await self.db.execute(query)
        return result.scalar() or 0
    
    async def get_user_organizations(self, user_id: uuid.UUID) -> List[OrganizationMember]:
        """
        Get all organizations a user is in (with their membership info).
        Excludes deleted organizations.
        
        Args:
            user_id: User ID
            
        Returns:
            List of organization memberships
        """
        result = await self.db.execute(
            select(OrganizationMember)
            .join(Organization, OrganizationMember.org_id == Organization.id)
            .where(
                OrganizationMember.user_id == user_id,
                Organization.is_deleted == False
            )
            .options(joinedload(OrganizationMember.organization))
            .order_by(OrganizationMember.joined_at.desc())
        )
        return list(result.unique().scalars().all())
    
    async def get_user_org_ids(self, user_id: uuid.UUID) -> List[uuid.UUID]:
        """
        Get list of organization IDs a user belongs to (excluding deleted organizations).
        
        Args:
            user_id: User ID
            
        Returns:
            List of organization IDs
        """
        result = await self.db.execute(
            select(OrganizationMember.org_id)
            .join(Organization, OrganizationMember.org_id == Organization.id)
            .where(
                OrganizationMember.user_id == user_id,
                Organization.is_deleted == False
            )
        )
        return [row[0] for row in result.all()]
