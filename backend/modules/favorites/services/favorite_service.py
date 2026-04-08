"""Favorite service business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from modules.favorites.repositories.favorite_repository import FavoriteRepository
from modules.knowledge.repositories.kb_repository import KnowledgeBaseRepository
from modules.knowledge.repositories.document_repository import DocumentRepository
from modules.knowledge.repositories.kb_subscription_repository import KBSubscriptionRepository
from modules.organization.repositories.organization_member_repository import OrganizationMemberRepository
from modules.favorites.entities.favorite import Favorite
from modules.knowledge.entities.knowledge_base import KnowledgeBase
from typing import List, Tuple, Optional
import uuid
import logging

logger = logging.getLogger(__name__)


class FavoriteService:
    """Service for favorite operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.favorite_repo = FavoriteRepository(db)
        self.kb_repo = KnowledgeBaseRepository(db)
        self.doc_repo = DocumentRepository(db)
        self.subscription_repo = KBSubscriptionRepository(db)
        self.org_member_repo = OrganizationMemberRepository(db)
    
    async def _check_kb_access(self, kb_id: str, user_id: str) -> Optional[KnowledgeBase]:
        """
        Check if user has access to a knowledge base.
        Returns the KB if accessible, None otherwise.
        
        Access is granted if:
        1. User owns the KB
        2. KB is public (visibility == 'public')
        3. KB is shared to an organization the user belongs to
        """
        # 1. Check if user owns the KB
        kb = await self.kb_repo.get_by_id(kb_id, user_id)
        if kb:
            return kb
        
        # 2. Get KB without owner check
        kb = await self.kb_repo.get_by_id_any(kb_id)
        if not kb:
            return None
        
        # 3. Check if KB is public
        if kb.visibility == 'public':
            return kb
        
        # 4. Check if KB is shared to user's organizations
        if kb.visibility == 'organization':
            user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            user_org_ids = await self.org_member_repo.get_user_org_ids(user_uuid)
            
            if not kb.shared_to_orgs or not user_org_ids:
                return None
            
            # Convert both to string sets for comparison
            shared_org_ids = set(str(org_id) for org_id in kb.shared_to_orgs)
            user_orgs_set = set(str(org_id) for org_id in user_org_ids)
            
            if shared_org_ids.intersection(user_orgs_set):
                return kb
        
        return None
    
    async def favorite_kb(
        self,
        kb_id: str,
        user_id: str,
        source: str = Favorite.SOURCE_MANUAL
    ) -> dict:
        """Favorite a knowledge base."""
        # Verify KB exists and user has access
        kb = await self._check_kb_access(kb_id, user_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found or not accessible"}}
            )
        
        await self.favorite_repo.add_favorite(
            user_id,
            Favorite.ITEM_TYPE_KB,
            kb_id,
            source
        )
        logger.info(f"User {user_id} favorited KB {kb_id} (source: {source})")
        return {"success": True}
    
    async def unfavorite_kb(self, kb_id: str, user_id: str) -> dict:
        """Unfavorite a knowledge base."""
        # Check if this favorite was from a subscription
        import uuid
        user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        kb_uuid = uuid.UUID(kb_id) if isinstance(kb_id, str) else kb_id
        
        from sqlalchemy import select
        favorite_result = await self.db.execute(
            select(Favorite).where(
                Favorite.user_id == user_uuid,
                Favorite.item_type == Favorite.ITEM_TYPE_KB,
                Favorite.item_id == kb_uuid
            )
        )
        favorite = favorite_result.scalar_one_or_none()
        
        # Remove the favorite
        success = await self.favorite_repo.remove_favorite(
            user_id,
            Favorite.ITEM_TYPE_KB,
            kb_id
        )
        
        if success:
            logger.info(f"User {user_id} unfavorited KB {kb_id}")
            
            # If this favorite was from a subscription, also unsubscribe
            # Use subscription_repo directly to avoid circular dependency
            if favorite and favorite.source == Favorite.SOURCE_SUBSCRIPTION:
                try:
                    await self.subscription_repo.unsubscribe(user_id, kb_id)
                    logger.info(f"Auto-unsubscribed user {user_id} from KB {kb_id}")
                except Exception as e:
                    logger.warning(f"Failed to auto-unsubscribe from KB {kb_id}: {e}")
        
        return {"success": success}
    
    async def favorite_document(
        self,
        doc_id: str,
        kb_id: str,
        user_id: str
    ) -> dict:
        """Favorite a document."""
        # Verify user has access to the KB (owned, public, or org-shared)
        kb = await self._check_kb_access(kb_id, user_id)
        if not kb:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Knowledge base not found or not accessible"}}
            )
        
        # Verify document exists in this KB
        doc = await self.doc_repo.get_by_id(doc_id, kb_id)
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Document not found"}}
            )
        
        await self.favorite_repo.add_favorite(
            user_id,
            Favorite.ITEM_TYPE_DOC,
            doc_id
        )
        logger.info(f"User {user_id} favorited document {doc_id}")
        return {"success": True}
    
    async def unfavorite_document(self, doc_id: str, user_id: str) -> dict:
        """Unfavorite a document."""
        success = await self.favorite_repo.remove_favorite(
            user_id,
            Favorite.ITEM_TYPE_DOC,
            doc_id
        )
        if success:
            logger.info(f"User {user_id} unfavorited document {doc_id}")
        return {"success": success}
    
    async def list_favorite_kbs(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[dict], int]:
        """List favorite knowledge bases with creator info."""
        from repositories.user_repository import UserRepository
        from modules.organization.repositories.organization_repository import OrganizationRepository
        
        user_repo = UserRepository(self.db)
        
        kbs, total = await self.favorite_repo.list_kb_favorites(user_id, page, page_size)
        
        # Enrich with creator info
        result = []
        for kb in kbs:
            kb_dict = kb.to_dict(include_owner=True)
            
            # Get creator info
            creator = await user_repo.get_by_id(kb.owner_id)
            if creator:
                kb_dict['creator_name'] = creator.name
                kb_dict['creator_avatar'] = creator.avatar
            
            # Add source info
            kb_dict['is_admin_recommended'] = kb.visibility == 'public' or kb.is_public
            kb_dict['from_organization'] = kb.visibility == 'organization'
            
            if kb.visibility == 'organization' and kb.shared_to_orgs:
                org_repo = OrganizationRepository(self.db)
                org = await org_repo.get_by_id(kb.shared_to_orgs[0])
                if org:
                    kb_dict['organization_name'] = org.name
            
            result.append(kb_dict)
        
        return result, total
    
    async def list_favorite_docs(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[dict], int]:
        """List favorite documents with KB info."""
        docs, total = await self.favorite_repo.list_doc_favorites(user_id, page, page_size)
        
        # Enrich with KB info
        result = []
        for doc in docs:
            doc_dict = doc.to_dict()
            # Get KB info
            kb = await self.db.get(KnowledgeBase, doc.kb_id)
            if kb:
                doc_dict["kbName"] = kb.name
                doc_dict["kbAvatar"] = kb.avatar or "/kb.png"
            result.append(doc_dict)
        
        return result, total
    
    async def check_favorites(
        self,
        user_id: str,
        items: List[dict]
    ) -> dict:
        """Batch check if items are favorited."""
        result = {}
        for item in items:
            item_type = item.get("type")
            item_id = item.get("id")
            if item_type and item_id:
                is_favorited = await self.favorite_repo.check_favorite(
                    user_id,
                    item_type,
                    item_id
                )
                result[f"{item_type}:{item_id}"] = is_favorited
        return result
