"""Knowledge Base service business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from modules.knowledge.repositories.kb_repository import KnowledgeBaseRepository
from modules.knowledge.repositories.document_repository import DocumentRepository
from modules.knowledge.repositories.kb_subscription_repository import KBSubscriptionRepository
from modules.organization.repositories.organization_member_repository import OrganizationMemberRepository
from repositories.user_repository import UserRepository
from utils.external_services import DocumentProcessService
from utils.es_utils import get_user_es_index
from utils.avatar_security import validate_avatar_upload
from config.settings import settings
from typing import List, Tuple, Optional
import logging
import uuid

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """Service for knowledge base operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.kb_repo = KnowledgeBaseRepository(db)
        self.doc_repo = DocumentRepository(db)
        self.subscription_repo = KBSubscriptionRepository(db)
        self.org_member_repo = OrganizationMemberRepository(db)
        self.user_repo = UserRepository(db)
    
    async def list_kbs(
        self,
        user_id: str,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[dict], int]:
        """List knowledge bases for user."""
        kbs, total = await self.kb_repo.list_kbs(user_id, query, page, page_size)
        return [kb.to_dict() for kb in kbs], total
    
    async def _verify_kb_write_access(self, kb_id: str, user_id: str) -> 'KnowledgeBase':
        """
        Verify user has WRITE access to knowledge base.
        Only owner and admin users have write permissions.
        
        Returns:
            Knowledge base object if user has write access
        
        Raises:
            HTTPException: If knowledge base not found or user has no write permission
        """
        from modules.knowledge.entities.knowledge_base import KnowledgeBase
        
        # Check if user is admin
        user = await self.user_repo.get_by_id(uuid.UUID(user_id))
        is_admin = user and user.is_admin
        
        # Try to get as owner
        kb = await self.kb_repo.get_by_id(kb_id, user_id)
        
        # If not owner but is admin, get the KB
        if not kb and is_admin:
            kb = await self.kb_repo.get_by_id_any(kb_id)
        
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "Only the knowledge base owner or admin can perform this action"}}
            )
        
        return kb
    
    async def get_kb(self, kb_id: str, user_id: str) -> dict:
        """Get knowledge base details."""
        kb = await self.kb_repo.get_by_id(kb_id, user_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found"}}
            )
        return kb.to_dict()
    
    async def get_kb_info(self, kb_id: str, user_id: str) -> dict:
        """
        Get knowledge base info.
        - Admin users: can access any knowledge base
        - Owners: can access their own knowledge bases
        - Organization members: can access organization-shared knowledge bases
        - Everyone: can access public knowledge bases
        """
        # Check if user is admin
        user = await self.user_repo.get_by_id(uuid.UUID(user_id))
        is_admin = user and user.is_admin
        
        # Try to get as owner first
        kb = await self.kb_repo.get_by_id(kb_id, user_id)
        is_owner = kb is not None
        
        # If not owner and admin, get any KB
        if not kb and is_admin:
            kb = await self.kb_repo.get_by_id_any(kb_id)
        
        # If not owner and not admin, check organization-shared or public KB
        if not kb:
            # Get user's organizations
            user_org_ids = await self.org_member_repo.get_user_org_ids(uuid.UUID(user_id))
            
            # Try to get the KB without access check
            kb = await self.kb_repo.get_by_id_any(kb_id)
            
            if kb:
                # Check if user has access
                has_access = False
                
                # 1. Public KB - everyone can access
                if kb.visibility == 'public':
                    has_access = True
                
                # 2. Organization-shared KB - check if user is in any shared organization
                elif kb.visibility == 'organization' and user_org_ids:
                    # Check if any of user's org IDs is in the shared_to_orgs
                    shared_org_ids = set(kb.shared_to_orgs or [])
                    user_orgs_set = set(user_org_ids)
                    if shared_org_ids.intersection(user_orgs_set):
                        has_access = True
                
                if not has_access:
                    kb = None
            
            if not kb:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found or not accessible"}}
                )
        
        # Check if user is subscribed (if not owner)
        is_subscribed = False
        if not is_owner:
            subscription = await self.subscription_repo.get_subscription(user_id, kb_id)
            is_subscribed = subscription is not None
        
        # Serialize BEFORE any database operations that might expire the object
        result = kb.to_dict(include_owner=True)
        result["isOwner"] = is_owner
        result["isSubscribed"] = is_subscribed
        
        # Update view count and last_viewed_at for subscribed users (after serialization)
        if not is_owner and is_subscribed:
            await self.kb_repo.increment_view_count(kb_id)
            await self.subscription_repo.update_last_viewed(user_id, kb_id)
        
        return result
    
    async def create_kb(
        self,
        user_id: str,
        name: str,
        description: Optional[str],
        category: str = "其它"
    ) -> dict:
        """Create a new knowledge base."""
        # Validate name length (max 16 characters, Chinese counts as 2)
        name_length = sum(2 if ord(c) >= 0x4e00 and ord(c) <= 0x9fff else 1 for c in name)
        if name_length > 16:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": "NAME_TOO_LONG", "message": "知识库名称过长，最多8个汉字或16个字母"}}
            )
        
        # Validate description length (max 60 characters, Chinese counts as 2)
        if description:
            desc_length = sum(2 if ord(c) >= 0x4e00 and ord(c) <= 0x9fff else 1 for c in description)
            if desc_length > 60:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": "DESC_TOO_LONG", "message": "知识库描述过长，最多30个汉字或60个字母"}}
                )
        
        # Check if name already exists for this user
        name_exists = await self.kb_repo.check_name_exists(user_id, name)
        if name_exists:
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": "NAME_CONFLICT", "message": "您已有同名知识库，请使用其他名称"}}
            )
        
        kb = await self.kb_repo.create(user_id, name, description, category)
        logger.info(f"Created knowledge base: {kb.id} with category: {category}")
        return {"id": str(kb.id)}
    
    async def update_kb(
        self,
        kb_id: str,
        user_id: str,
        **kwargs
    ) -> dict:
        """
        Update knowledge base.
        Only owner and admin users can update knowledge bases.
        """
        # Verify write access (owner or admin only)
        kb = await self._verify_kb_write_access(kb_id, user_id)
        
        # Validate name if provided
        if 'name' in kwargs and kwargs['name']:
            name = kwargs['name']
            # Validate name length
            name_length = sum(2 if ord(c) >= 0x4e00 and ord(c) <= 0x9fff else 1 for c in name)
            if name_length > 16:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": "NAME_TOO_LONG", "message": "知识库名称过长，最多8个汉字或16个字母"}}
                )
            # Check if name already exists (exclude current KB)
            name_exists = await self.kb_repo.check_name_exists(str(kb.owner_id), name, kb_id)
            if name_exists:
                raise HTTPException(
                    status_code=409,
                    detail={"error": {"code": "NAME_CONFLICT", "message": "您已有同名知识库，请使用其他名称"}}
                )
        
        # Validate description if provided
        if 'description' in kwargs and kwargs['description']:
            description = kwargs['description']
            desc_length = sum(2 if ord(c) >= 0x4e00 and ord(c) <= 0x9fff else 1 for c in description)
            if desc_length > 60:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": "DESC_TOO_LONG", "message": "知识库描述过长，最多30个汉字或60个字母"}}
                )
        
        await self.kb_repo.update(kb, **kwargs)
        return {"success": True}
    
    async def delete_kb(self, kb_id: str, user_id: str):
        """
        Delete knowledge base and all its documents.
        Only owner and admin users can delete knowledge bases.
        """
        # Verify write access (owner or admin only)
        kb = await self._verify_kb_write_access(kb_id, user_id)
        
        # Always clean up ES documents from the KB owner's index.
        user_index = get_user_es_index(str(kb.owner_id))
        
        # Get all documents for cleanup
        documents, _ = await self.doc_repo.list_documents(kb_id)
        
        # Delete from ES (using user-level index)
        for doc in documents:
            try:
                await DocumentProcessService.delete_document_from_es(
                    str(doc.id),
                    user_index
                )
            except Exception as e:
                logger.warning(f"Failed to delete doc {doc.id} from ES: {e}")
        
        # Delete KB (will cascade delete documents in DB)
        await self.kb_repo.delete(kb)
        logger.info(f"Deleted knowledge base: {kb_id}")
    
    async def get_quota(self, user_id: str) -> dict:
        """Get storage quota for user."""
        used_bytes = await self.kb_repo.calculate_total_size(user_id)
        return {
            "usedBytes": used_bytes,
            "limitBytes": 500000000000  # 500GB, should be configurable per user
        }
    
    async def upload_avatar(
        self,
        kb_id: str,
        user_id: str,
        file_data: bytes,
        filename: str,
        content_type: str
    ) -> dict:
        """Upload knowledge base avatar."""
        from utils.minio_client import upload_file
        
        # Verify KB ownership
        kb = await self.kb_repo.get_by_id(kb_id, user_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found"}}
            )
        
        try:
            file_extension, normalized_content_type = validate_avatar_upload(
                file_data=file_data,
                filename=filename,
                content_type=content_type,
                max_bytes=settings.MAX_AVATAR_SIZE,
            )
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                exc.detail["error"]["code"] = "VALIDATION_ERROR"
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc
        
        # Upload to MinIO
        object_name = f"kb_avatars/{user_id}/{kb_id}.{file_extension}"
        await upload_file(object_name, file_data, normalized_content_type)
        
        # Generate presigned URL for access (valid for 7 days)
        from utils.minio_client import get_file_url
        avatar_url = get_file_url(object_name, expires_seconds=7*24*3600)
        
        # Update KB avatar with presigned URL
        await self.kb_repo.update(kb, avatar=avatar_url)
        
        logger.info(f"Updated KB {kb_id} avatar: {avatar_url}")
        return {"avatarUrl": avatar_url}
    
    # ============ Public Sharing & Subscription Features ============
    
    async def toggle_public(self, kb_id: str, user_id: str) -> dict:
        """
        Toggle public status of a knowledge base.
        Only owner and admin users can toggle public status.
        """
        # Verify write access (owner or admin only)
        kb = await self._verify_kb_write_access(kb_id, user_id)
        
        kb = await self.kb_repo.toggle_public(kb)
        logger.info(f"KB {kb_id} visibility: {kb.visibility}")
        return {
            "visibility": kb.visibility,
            "subscribersCount": kb.subscribers_count
        }
    
    async def subscribe_kb(self, kb_id: str, user_id: str) -> dict:
        """Subscribe to a public knowledge base."""
        # Check if KB exists and is public
        kb = await self.kb_repo.get_by_id_public(kb_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Public knowledge base not found"}}
            )
        
        # Cannot subscribe to own KB
        if str(kb.owner_id) == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "VALIDATION_ERROR", "message": "Cannot subscribe to your own knowledge base"}}
            )
        
        # Subscribe
        await self.subscription_repo.subscribe(user_id, kb_id)
        logger.info(f"User {user_id} subscribed to KB {kb_id}")
        
        # Auto-favorite when subscribing
        from modules.favorites.services.favorite_service import FavoriteService
        from modules.favorites.entities.favorite import Favorite
        favorite_service = FavoriteService(self.db)
        try:
            await favorite_service.favorite_kb(
                kb_id,
                user_id,
                source=Favorite.SOURCE_SUBSCRIPTION
            )
            logger.info(f"Auto-favorited KB {kb_id} for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to auto-favorite KB {kb_id}: {e}")
        
        # Return updated count
        kb = await self.kb_repo.get_by_id_public(kb_id)
        return {"subscribersCount": kb.subscribers_count}
    
    async def unsubscribe_kb(self, kb_id: str, user_id: str) -> dict:
        """Unsubscribe from a knowledge base."""
        success = await self.subscription_repo.unsubscribe(user_id, kb_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Subscription not found"}}
            )
        
        logger.info(f"User {user_id} unsubscribed from KB {kb_id}")
        
        # Auto-remove favorite if it was from subscription
        from modules.favorites.services.favorite_service import FavoriteService
        from modules.favorites.entities.favorite import Favorite
        favorite_service = FavoriteService(self.db)
        try:
            # Convert string IDs to UUID
            user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            kb_uuid = uuid.UUID(kb_id) if isinstance(kb_id, str) else kb_id
            
            # Check if favorite exists and is from subscription
            favorite = await self.db.execute(
                select(Favorite).where(
                    Favorite.user_id == user_uuid,
                    Favorite.item_type == Favorite.ITEM_TYPE_KB,
                    Favorite.item_id == kb_uuid,
                    Favorite.source == Favorite.SOURCE_SUBSCRIPTION
                )
            )
            fav = favorite.scalar_one_or_none()
            if fav:
                await favorite_service.unfavorite_kb(kb_id, user_id)
                logger.info(f"Auto-removed favorite KB {kb_id} for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to auto-remove favorite KB {kb_id}: {e}")
        
        # Return updated count
        kb = await self.kb_repo.get_by_id_public(kb_id)
        return {"subscribersCount": kb.subscribers_count if kb else 0}
    
    async def check_subscription(self, kb_id: str, user_id: str) -> dict:
        """Check if user is subscribed to a knowledge base."""
        subscription = await self.subscription_repo.get_subscription(user_id, kb_id)
        return {
            "isSubscribed": subscription is not None,
            "subscribedAt": subscription.subscribed_at.isoformat() if subscription else None
        }
    
    async def list_user_subscriptions(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[dict], int]:
        """List all knowledge bases subscribed by a user."""
        kbs, total = await self.subscription_repo.list_user_subscriptions(user_id, page, page_size)
        return [kb.to_dict(include_owner=True) for kb in kbs], total
    
    async def list_public_kbs(
        self,
        category: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[dict], int]:
        """List public knowledge bases."""
        kbs, total = await self.kb_repo.list_public_kbs(category, query, page, page_size)
        return [kb.to_dict(include_owner=True) for kb in kbs], total
    
    async def list_featured_kbs(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 30
    ) -> Tuple[List[dict], int]:
        """
        List featured knowledge bases (2025年度精选).
        Shows all visible knowledge bases based on user permissions.
        """
        # Get user info
        user = await self.user_repo.get_by_id(uuid.UUID(user_id))
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "用户不存在"}}
            )
        
        # Get user's organizations
        user_org_ids = await self.org_member_repo.get_user_org_ids(uuid.UUID(user_id))
        
        # Get featured KBs with user's visibility permissions
        kbs, total = await self.kb_repo.list_featured_kbs(
            user_id=uuid.UUID(user_id),
            user_org_ids=user_org_ids,
            is_admin=user.is_admin,
            page=page,
            page_size=page_size
        )
        
        # Enrich with creator info
        result = []
        for kb in kbs:
            kb_dict = kb.to_dict(include_owner=True)
            # Get creator info
            creator = await self.user_repo.get_by_id(kb.owner_id)
            if creator:
                kb_dict['creator_name'] = creator.name
                kb_dict['creator_avatar'] = creator.avatar
            kb_dict['is_admin_recommended'] = kb.visibility == 'public'
            kb_dict['from_organization'] = kb.visibility == 'organization'
            if kb.visibility == 'organization' and kb.shared_to_orgs:
                from modules.organization.repositories.organization_repository import OrganizationRepository
                org_repo = OrganizationRepository(self.db)
                org = await org_repo.get_by_id(kb.shared_to_orgs[0])
                if org:
                    kb_dict['organization_name'] = org.name
            result.append(kb_dict)
        
        return result, total
    
    async def get_categories_stats(self) -> List[dict]:
        """Get statistics for each category."""
        return await self.kb_repo.get_categories_stats()
    
    # ============ Organization & Visibility Features ============
    
    async def list_plaza_kbs(
        self,
        user_id: str,
        category: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[dict], int]:
        """
        List knowledge bases in plaza based on user permissions.
        - Admin users see all public KBs
        - Regular users see: admin-shared public KBs + org-shared KBs from their organizations
        """
        # Get user info
        user = await self.user_repo.get_by_id(uuid.UUID(user_id))
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "用户不存在"}}
            )
        
        # Get user's organizations
        user_org_ids = await self.org_member_repo.get_user_org_ids(uuid.UUID(user_id))
        
        # Get visible KBs based on user permissions
        kbs, total = await self.kb_repo.get_visible_kbs_for_user(
            user_id=uuid.UUID(user_id),
            user_org_ids=user_org_ids,
            is_admin=user.is_admin,
            category=category,
            query=query,
            page=page,
            page_size=page_size
        )
        
        # Enrich KB data with creator and badge information
        result = []
        for kb in kbs:
            kb_dict = kb.to_dict(include_owner=True)
            
            # Add creator info
            creator = await self.user_repo.get_by_id(kb.owner_id)
            if creator:
                kb_dict['creator_name'] = creator.name
                kb_dict['creator_avatar'] = creator.avatar
            
            kb_dict['is_admin_recommended'] = kb.visibility == 'public'
            kb_dict['from_organization'] = kb.visibility == 'organization'
            
            # Add badge information
            if kb.visibility == 'public':
                kb_dict['badge'] = 'admin_recommended'
                kb_dict['badge_text'] = '管理员推荐'
            elif kb.visibility == 'organization' and kb.shared_to_orgs:
                # Find which org this KB is shared from
                # Both kb.shared_to_orgs and user_org_ids are List[UUID]
                matching_orgs = set(kb.shared_to_orgs) & set(user_org_ids)
                if matching_orgs:
                    # Get org name (just use first matching org for simplicity)
                    from modules.organization.repositories.organization_repository import OrganizationRepository
                    org_repo = OrganizationRepository(self.db)
                    org = await org_repo.get_by_id(list(matching_orgs)[0])
                    if org:
                        kb_dict['badge'] = 'organization'
                        kb_dict['badge_text'] = f'来自 {org.name}'
                        kb_dict['source_org_name'] = org.name
                        kb_dict['organization_name'] = org.name
            
            result.append(kb_dict)
        
        return result, total
    
    async def update_visibility(
        self,
        kb_id: str,
        user_id: str,
        visibility: str,
        shared_to_orgs: Optional[List[str]] = None
    ) -> dict:
        """
        Update knowledge base visibility.
        - private: Only owner can access
        - organization: Shared to specified organizations
        - public: Globally visible (admin only)
        
        Only owner and admin users can update visibility.
        """
        # Verify write access (owner or admin only)
        kb = await self._verify_kb_write_access(kb_id, user_id)
        
        # Check if setting to public (admin only)
        if visibility == 'public':
            user = await self.user_repo.get_by_id(uuid.UUID(user_id))
            if not user or not user.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": "FORBIDDEN", "message": "只有管理员可以将知识库设置为全局公开"}}
                )
        
        # Update visibility
        await self.kb_repo.update_visibility(uuid.UUID(kb_id), visibility)
        
        # Update shared organizations if visibility is organization
        if visibility == 'organization' and shared_to_orgs is not None:
            org_uuids = [uuid.UUID(org_id) for org_id in shared_to_orgs]
            await self.kb_repo.set_shared_organizations(uuid.UUID(kb_id), org_uuids)
        elif visibility != 'organization':
            # Clear shared orgs if not organization visibility
            await self.kb_repo.set_shared_organizations(uuid.UUID(kb_id), [])
        
        # Return updated KB
        updated_kb = await self.kb_repo.get_by_id(kb_id, user_id)
        return updated_kb.to_dict()
    
    async def share_to_organizations(
        self,
        kb_id: str,
        user_id: str,
        org_ids: List[str]
    ) -> dict:
        """
        Share knowledge base to specific organizations.
        Automatically sets visibility to 'organization'.
        Only owner and admin users can share knowledge bases.
        """
        logger.info(f"Sharing KB {kb_id} to organizations: {org_ids} by user {user_id}")
        
        # Verify write access (owner or admin only)
        kb = await self._verify_kb_write_access(kb_id, user_id)
        
        # Verify user is member of all specified organizations
        user_org_ids = await self.org_member_repo.get_user_org_ids(uuid.UUID(user_id))
        user_org_ids_str = [str(org_id) for org_id in user_org_ids]
        
        logger.info(f"User {user_id} is member of organizations: {user_org_ids_str}")
        
        for org_id in org_ids:
            if org_id not in user_org_ids_str:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error": {"code": "INVALID_OPERATION", "message": f"您不是组织 {org_id} 的成员"}}
                )
        
        # Update visibility to organization
        await self.kb_repo.update_visibility(uuid.UUID(kb_id), 'organization')
        
        # Share to organizations
        org_uuids = [uuid.UUID(org_id) for org_id in org_ids]
        await self.kb_repo.share_to_organizations(uuid.UUID(kb_id), org_uuids)
        
        logger.info(f"Successfully shared KB {kb_id} to organizations: {org_ids}")
        
        # Return updated KB
        updated_kb = await self.kb_repo.get_by_id(kb_id, user_id)
        return updated_kb.to_dict()
    
    async def get_shared_status(
        self,
        kb_id: str,
        user_id: str
    ) -> dict:
        """Get knowledge base visibility and sharing status."""
        # Get KB
        kb = await self.kb_repo.get_by_id(kb_id, user_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "知识库不存在"}}
            )
        
        # Check if user is owner
        is_owner = str(kb.owner_id) == user_id
        
        # Get shared organizations info
        shared_orgs = []
        if kb.shared_to_orgs:
            from modules.organization.repositories.organization_repository import OrganizationRepository
            org_repo = OrganizationRepository(self.db)
            for org_id in kb.shared_to_orgs:
                # org_id is already a UUID object from PostgreSQL, convert to UUID if needed
                if isinstance(org_id, str):
                    org_id = uuid.UUID(org_id)
                org = await org_repo.get_by_id(org_id)
                if org:
                    shared_orgs.append({
                        "id": str(org.id),
                        "name": org.name,
                        "avatar": org.avatar
                    })
        
        return {
            "kb_id": kb_id,
            "visibility": kb.visibility,
            "shared_to_orgs": shared_orgs,
            "is_owner": is_owner,
            "can_modify": is_owner
        }
