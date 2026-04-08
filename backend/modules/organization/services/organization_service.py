"""Organization service business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from modules.organization.repositories.organization_repository import OrganizationRepository
from modules.organization.repositories.organization_member_repository import OrganizationMemberRepository
from modules.knowledge.repositories.kb_repository import KnowledgeBaseRepository
from repositories.user_repository import UserRepository
from modules.organization.entities.organization import Organization
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone
import uuid
import logging

logger = logging.getLogger(__name__)


class OrganizationService:
    """Service for organization operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.org_repo = OrganizationRepository(db)
        self.member_repo = OrganizationMemberRepository(db)
        self.kb_repo = KnowledgeBaseRepository(db)
        self.user_repo = UserRepository(db)
    
    async def create_organization(
        self,
        user_id: uuid.UUID,
        name: str,
        description: Optional[str] = None,
        avatar: Optional[str] = None
    ) -> Dict:
        """
        Create a new organization.
        
        Args:
            user_id: User ID
            name: Organization name
            description: Organization description
            avatar: Avatar URL
            
        Returns:
            Created organization dict
        """
        # Get user and check permissions
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "User not found"}}
            )
        
        # Check if user can create organization
        can_create, reason = user.can_create_organization()
        if not can_create:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": reason}}
            )
        
        # Get user's limits
        limits = user.get_organization_limits()
        
        # Check current owned organizations count
        owned_orgs = await self.org_repo.get_by_owner(user_id)
        if len(owned_orgs) >= limits['can_create']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "LIMIT_EXCEEDED", "message": f"您最多只能创建 {limits['can_create']} 个组织"}}
            )
        
        # Generate unique organization code
        org_code = Organization.generate_org_code()
        
        # Ensure code is unique
        while await self.org_repo.get_by_code(org_code):
            org_code = Organization.generate_org_code()
        
        # Create organization
        org = await self.org_repo.create(
            owner_id=user_id,
            name=name,
            description=description,
            avatar=avatar,
            org_code=org_code,
            max_members=limits['max_members']
        )
        
        # Add owner as member with 'owner' role
        await self.member_repo.add_member(org.id, user_id, role='owner')
        
        # Re-query to load relationships (owner and members)
        org_with_relations = await self.org_repo.get_by_id(org.id)
        
        return org_with_relations.to_dict(include_members=True)
    
    async def get_organization(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Dict:
        """
        Get organization details.
        
        Args:
            org_id: Organization ID
            user_id: Requesting user ID
            
        Returns:
            Organization dict
        """
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Organization not found"}}
            )
        
        # Check if user is a member
        is_member = await self.member_repo.is_member(org_id, user_id)
        if not is_member:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "您不是该组织的成员"}}
            )
        
        return org.to_dict(include_members=True)
    
    async def list_my_organizations(self, user_id: uuid.UUID) -> Dict:
        """
        List user's organizations (owned and joined).
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with 'created' and 'joined' organization lists
        """
        # Get owned organizations
        owned_orgs = await self.org_repo.get_by_owner(user_id)
        
        # Get joined organizations
        joined_orgs = await self.org_repo.get_joined_by_user(user_id)
        
        created_list = []
        joined_list = []
        
        # Add owned organizations
        for org in owned_orgs:
            org_dict = org.to_dict(include_members=False)
            org_dict['role'] = 'owner'
            org_dict['is_owner'] = True
            created_list.append(org_dict)
        
        # Add joined organizations
        for org in joined_orgs:
            org_dict = org.to_dict(include_members=False)
            org_dict['role'] = 'member'
            org_dict['is_owner'] = False
            joined_list.append(org_dict)
        
        return {
            'created': created_list,
            'joined': joined_list
        }
    
    async def update_organization(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        avatar: Optional[str] = None
    ) -> Dict:
        """
        Update organization information.
        
        Args:
            org_id: Organization ID
            user_id: User ID
            name: New name
            description: New description
            avatar: New avatar
            
        Returns:
            Updated organization dict
        """
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Organization not found"}}
            )
        
        # Check if user is owner
        if org.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅组织所有者可以修改信息"}}
            )
        
        # Build update dict
        updates = {}
        if name is not None:
            updates['name'] = name
        if description is not None:
            updates['description'] = description
        if avatar is not None:
            updates['avatar'] = avatar
        
        if updates:
            updated_org = await self.org_repo.update(org_id, **updates)
            return updated_org.to_dict()
        
        return org.to_dict()
    
    async def delete_organization(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Dict:
        """
        Delete (dissolve) an organization.
        
        Args:
            org_id: Organization ID
            user_id: User ID
            
        Returns:
            Success message
        """
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Organization not found"}}
            )
        
        # Check if user is owner
        if org.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅组织所有者可以解散组织"}}
            )
        
        # 清理所有共享到该组织的知识库
        affected_kbs = await self.kb_repo.remove_org_from_all_kbs(org_id)
        if affected_kbs > 0:
            logger.info(f"Org {org_id} dissolved, removed from {affected_kbs} KB(s) shared_to_orgs")
        
        # Delete organization (soft delete)
        await self.org_repo.delete(org_id)
        
        return {"message": "组织已解散"}
    
    async def join_organization(self, user_id: uuid.UUID, org_code: str) -> Dict:
        """
        Join an organization using organization code.
        
        Args:
            user_id: User ID
            org_code: Organization code
            
        Returns:
            Organization dict
        """
        # Get user
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "User not found"}}
            )
        
        # Get organization by code
        org = await self.org_repo.get_by_code(org_code)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "INVALID_CODE", "message": "组织码不存在"}}
            )
        
        # Check if code is expired
        if org.is_code_expired():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "CODE_EXPIRED", "message": "组织码已过期"}}
            )
        
        # Check if already a member
        is_member = await self.member_repo.is_member(org.id, user_id)
        if is_member:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "ALREADY_MEMBER", "message": "您已经是该组织的成员"}}
            )
        
        # Check user's join limit
        limits = user.get_organization_limits()
        joined_count = await self.member_repo.count_user_organizations(user_id, role='member')
        
        if joined_count >= limits['can_join']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "LIMIT_EXCEEDED", "message": f"您最多只能加入 {limits['can_join']} 个组织"}}
            )
        
        # Check organization member limit
        can_add, reason = org.can_add_member()
        if not can_add:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "ORG_FULL", "message": reason}}
            )
        
        # Add member
        await self.member_repo.add_member(org.id, user_id, role='member')
        
        return org.to_dict()
    
    async def leave_organization(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Dict:
        """
        Leave an organization.
        
        Args:
            org_id: Organization ID
            user_id: User ID
            
        Returns:
            Success message
        """
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Organization not found"}}
            )
        
        # Check if user is owner
        if org.owner_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "OWNER_CANNOT_LEAVE", "message": "组织所有者不能退出组织，请先解散组织"}}
            )
        
        # Check if user is a member
        is_member = await self.member_repo.is_member(org_id, user_id)
        if not is_member:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "NOT_MEMBER", "message": "您不是该组织的成员"}}
            )
        
        # Remove member
        await self.member_repo.remove_member(org_id, user_id)
        
        # 清理该用户共享到该组织的知识库
        affected_kbs = await self.kb_repo.remove_org_from_user_kbs(user_id, org_id)
        if affected_kbs > 0:
            logger.info(f"User {user_id} left org {org_id}, removed org from {affected_kbs} KB(s)")
        
        return {"message": "已退出组织"}
    
    async def get_members(self, org_id: uuid.UUID, user_id: uuid.UUID) -> List[Dict]:
        """
        Get organization members.
        
        Args:
            org_id: Organization ID
            user_id: Requesting user ID
            
        Returns:
            List of members
        """
        # Check if user is a member
        is_member = await self.member_repo.is_member(org_id, user_id)
        if not is_member:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅组织成员可以查看成员列表"}}
            )
        
        members = await self.member_repo.get_members(org_id)
        return [member.to_dict() for member in members]
    
    async def remove_member(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        target_user_id: uuid.UUID
    ) -> Dict:
        """
        Remove a member from organization.
        
        Args:
            org_id: Organization ID
            user_id: Requesting user ID (must be owner)
            target_user_id: User ID to remove
            
        Returns:
            Success message
        """
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Organization not found"}}
            )
        
        # Check if user is owner
        if org.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅组织所有者可以移除成员"}}
            )
        
        # Cannot remove owner
        if target_user_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "CANNOT_REMOVE_OWNER", "message": "不能移除组织所有者"}}
            )
        
        # Remove member
        success = await self.member_repo.remove_member(org_id, target_user_id)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_MEMBER", "message": "该用户不是组织成员"}}
            )
        
        # 清理被移除用户共享到该组织的知识库
        affected_kbs = await self.kb_repo.remove_org_from_user_kbs(target_user_id, org_id)
        if affected_kbs > 0:
            logger.info(f"User {target_user_id} removed from org {org_id}, removed org from {affected_kbs} KB(s)")
        
        return {"message": "成员已移除"}
    
    async def regenerate_code(self, org_id: uuid.UUID, user_id: uuid.UUID) -> Dict:
        """
        Regenerate organization code.
        
        Args:
            org_id: Organization ID
            user_id: User ID (must be owner)
            
        Returns:
            Dict with new org_code
        """
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Organization not found"}}
            )
        
        # Check if user is owner
        if org.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅组织所有者可以重新生成组织码"}}
            )
        
        # Generate new unique code
        new_code = Organization.generate_org_code()
        while await self.org_repo.get_by_code(new_code):
            new_code = Organization.generate_org_code()
        
        # Update organization
        updated_org = await self.org_repo.regenerate_code(org_id, new_code)
        
        return {"org_code": updated_org.org_code}
    
    async def set_code_expiry(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        expires_at: Optional[datetime]
    ) -> Dict:
        """
        Set organization code expiry date.
        
        Args:
            org_id: Organization ID
            user_id: User ID (must be owner)
            expires_at: Expiry date (None for permanent)
            
        Returns:
            Success message
        """
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Organization not found"}}
            )
        
        # Check if user is owner
        if org.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "仅组织所有者可以设置组织码有效期"}}
            )
        
        # Update expiry
        await self.org_repo.update_code_expiry(org_id, expires_at)
        
        return {"message": "组织码有效期已更新"}
