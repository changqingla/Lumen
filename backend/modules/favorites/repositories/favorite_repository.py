"""Favorite repository for database operations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, desc
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Tuple
from modules.favorites.entities.favorite import Favorite
from modules.knowledge.entities.knowledge_base import KnowledgeBase
from modules.knowledge.entities.document import Document
from modules.knowledge.repositories.kb_repository import knowledge_base_access_condition
import logging
import uuid

logger = logging.getLogger(__name__)


class FavoriteRepository:
    """Repository for Favorite model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def add_favorite(
        self,
        user_id: str,
        item_type: str,
        item_id: str,
        source: str = Favorite.SOURCE_MANUAL
    ) -> Favorite:
        """Add a favorite (idempotent - returns existing if already exists)."""
        # Check if already exists
        existing = await self.get_favorite(user_id, item_type, item_id)
        if existing:
            logger.info(f"Favorite already exists: user={user_id}, type={item_type}, item={item_id}")
            return existing
        
        try:
            # Convert string IDs to UUID
            user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            item_uuid = uuid.UUID(item_id) if isinstance(item_id, str) else item_id
            
            favorite = Favorite(
                user_id=user_uuid,
                item_type=item_type,
                item_id=item_uuid,
                source=source
            )
            self.db.add(favorite)
            await self.db.commit()
            await self.db.refresh(favorite)
            logger.info(f"Created favorite: user={user_id}, type={item_type}, item={item_id}, source={source}")
            return favorite
        except IntegrityError:
            await self.db.rollback()
            # Race condition: another request created it
            existing = await self.get_favorite(user_id, item_type, item_id)
            if existing:
                return existing
            raise
    
    async def remove_favorite(
        self,
        user_id: str,
        item_type: str,
        item_id: str
    ) -> bool:
        """Remove a favorite."""
        # Convert string IDs to UUID
        user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        item_uuid = uuid.UUID(item_id) if isinstance(item_id, str) else item_id
        
        stmt = delete(Favorite).where(
            Favorite.user_id == user_uuid,
            Favorite.item_type == item_type,
            Favorite.item_id == item_uuid
        )
        
        result = await self.db.execute(stmt)
        await self.db.commit()
        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Removed favorite: user={user_id}, type={item_type}, item={item_id}")
        else:
            logger.warning(f"Failed to remove favorite (not found): user={user_id}, type={item_type}, item={item_id}")
        return deleted
    
    async def get_favorite(
        self,
        user_id: str,
        item_type: str,
        item_id: str
    ) -> Optional[Favorite]:
        """Get a specific favorite."""
        # Convert string IDs to UUID
        user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        item_uuid = uuid.UUID(item_id) if isinstance(item_id, str) else item_id
        
        result = await self.db.execute(
            select(Favorite).where(
                Favorite.user_id == user_uuid,
                Favorite.item_type == item_type,
                Favorite.item_id == item_uuid
            )
        )
        return result.scalar_one_or_none()
    
    async def list_kb_favorites(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        user_org_ids: Optional[List[uuid.UUID]] = None,
        is_admin: bool = False,
    ) -> Tuple[List[KnowledgeBase], int]:
        """List favorite knowledge bases that remain accessible to the user."""
        # Convert string ID to UUID
        user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        
        stmt = (
            select(KnowledgeBase)
            .join(
                Favorite,
                (Favorite.item_id == KnowledgeBase.id) &
                (Favorite.item_type == Favorite.ITEM_TYPE_KB)
            )
            .where(
                Favorite.user_id == user_uuid,
                knowledge_base_access_condition(
                    user_uuid,
                    user_org_ids,
                    is_admin=is_admin,
                ),
            )
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar()

        stmt = (
            stmt
            .order_by(desc(Favorite.created_at))
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        
        result = await self.db.execute(stmt)
        kbs = result.scalars().all()
        
        return list(kbs), total or 0
    
    async def list_doc_favorites(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        user_org_ids: Optional[List[uuid.UUID]] = None,
        is_admin: bool = False,
    ) -> Tuple[List[Document], int]:
        """List favorite documents whose knowledge bases remain accessible."""
        # Convert string ID to UUID
        user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        
        stmt = (
            select(Document)
            .join(
                Favorite,
                (Favorite.item_id == Document.id) &
                (Favorite.item_type == Favorite.ITEM_TYPE_DOC)
            )
            .join(KnowledgeBase, Document.kb_id == KnowledgeBase.id)
            .where(
                Favorite.user_id == user_uuid,
                knowledge_base_access_condition(
                    user_uuid,
                    user_org_ids,
                    is_admin=is_admin,
                ),
            )
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar()

        stmt = (
            stmt
            .order_by(desc(Favorite.created_at))
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        
        result = await self.db.execute(stmt)
        docs = result.scalars().all()
        
        return list(docs), total or 0
    
    async def check_favorite(
        self,
        user_id: str,
        item_type: str,
        item_id: str
    ) -> bool:
        """Check if an item is favorited."""
        favorite = await self.get_favorite(user_id, item_type, item_id)
        return favorite is not None
