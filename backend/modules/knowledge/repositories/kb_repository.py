"""Knowledge Base repository for database operations with visibility control."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_, and_, true
from typing import Optional, List, Tuple
from modules.knowledge.entities.knowledge_base import KnowledgeBase, KNOWLEDGE_CATEGORIES
from modules.knowledge.entities.document import Document
from datetime import datetime
import logging
import uuid


logger = logging.getLogger(__name__)


def knowledge_base_access_condition(
    user_id: uuid.UUID | str,
    user_org_ids: Optional[List[uuid.UUID]] = None,
    *,
    is_admin: bool = False,
):
    """Build the canonical SQL predicate for read access to a knowledge base."""
    if is_admin:
        return true()

    user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    conditions = [
        KnowledgeBase.owner_id == user_uuid,
        KnowledgeBase.visibility == "public",
    ]
    if user_org_ids:
        conditions.append(
            and_(
                KnowledgeBase.visibility == "organization",
                KnowledgeBase.shared_to_orgs.overlap(list(user_org_ids)),
            )
        )
    return or_(*conditions)


class KnowledgeBaseRepository:
    """Repository for KnowledgeBase model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_by_id(self, kb_id: str, owner_id: str) -> Optional[KnowledgeBase]:
        """Get knowledge base by ID for specific owner."""
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def get_accessible_by_id(
        self,
        kb_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
        user_org_ids: Optional[List[uuid.UUID]] = None,
        *,
        is_admin: bool = False,
    ) -> Optional[KnowledgeBase]:
        """Get a knowledge base only when the caller has read access."""
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                knowledge_base_access_condition(
                    user_id,
                    user_org_ids,
                    is_admin=is_admin,
                ),
            )
        )
        return result.scalar_one_or_none()

    async def get_writable_by_id(
        self,
        kb_id: uuid.UUID | str,
        user_id: uuid.UUID | str,
        *,
        is_admin: bool = False,
    ) -> Optional[KnowledgeBase]:
        """Get a knowledge base only when the caller is its owner or an admin."""
        user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
        write_condition = true() if is_admin else KnowledgeBase.owner_id == user_uuid
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                write_condition,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_owner_and_name(self, owner_id: str, name: str) -> Optional[KnowledgeBase]:
        """Get a knowledge base by owner and exact name."""
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.owner_id == owner_id,
                func.lower(KnowledgeBase.name) == func.lower(name),
            )
        )
        return result.scalar_one_or_none()
    
    async def check_name_exists(self, owner_id: str, name: str, exclude_kb_id: Optional[str] = None) -> bool:
        """
        Check if a knowledge base with the same name already exists for the user.
        
        Args:
            owner_id: Owner user ID
            name: Knowledge base name to check
            exclude_kb_id: KB ID to exclude (for update operations)
            
        Returns:
            True if name exists, False otherwise
        """
        query = select(KnowledgeBase).where(
            KnowledgeBase.owner_id == owner_id,
            func.lower(KnowledgeBase.name) == func.lower(name)
        )
        if exclude_kb_id:
            query = query.where(KnowledgeBase.id != exclude_kb_id)
        
        result = await self.db.execute(query)
        return result.scalar_one_or_none() is not None
    
    async def get_by_id_public(self, kb_id: str) -> Optional[KnowledgeBase]:
        """Get public knowledge base by ID (no owner check, using visibility field)."""
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.visibility == 'public'
            )
        )
        return result.scalar_one_or_none()
    
    async def get_by_id_any(self, kb_id: str) -> Optional[KnowledgeBase]:
        """Get knowledge base by ID without any access checks (for admin users)."""
        result = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        return result.scalar_one_or_none()
    
    async def list_kbs(
        self,
        owner_id: str,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[KnowledgeBase], int]:
        """List knowledge bases for user."""
        stmt = select(KnowledgeBase).where(KnowledgeBase.owner_id == owner_id)
        
        if query:
            stmt = stmt.where(KnowledgeBase.name.ilike(f"%{query}%"))
        
        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar()
        
        # Paginate
        stmt = stmt.order_by(KnowledgeBase.created_at.desc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        
        result = await self.db.execute(stmt)
        kbs = result.scalars().all()
        
        return list(kbs), total or 0
    
    async def create(
        self,
        owner_id: str,
        name: str,
        description: Optional[str],
        category: str = "其它"
    ) -> KnowledgeBase:
        """Create a new knowledge base."""
        # Validate category
        if category not in KNOWLEDGE_CATEGORIES:
            category = "其它"
        
        kb = KnowledgeBase(
            owner_id=owner_id,
            name=name,
            description=description,
            category=category
        )
        self.db.add(kb)
        await self.db.commit()
        await self.db.refresh(kb)
        return kb
    
    async def update(self, kb: KnowledgeBase, **kwargs) -> KnowledgeBase:
        """Update knowledge base fields."""
        for key, value in kwargs.items():
            if hasattr(kb, key) and value is not None:
                setattr(kb, key, value)
        await self.db.commit()
        await self.db.refresh(kb)
        return kb
    
    async def delete(self, kb: KnowledgeBase):
        """Delete a knowledge base."""
        await self.db.delete(kb)
        await self.db.commit()
    
    async def increment_contents_count(self, kb_id: str, delta: int = 1):
        """Increment contents count."""
        kb = await self.db.get(KnowledgeBase, kb_id)
        if kb:
            kb.contents_count += delta
            kb.last_updated_at = datetime.utcnow()
            await self.db.commit()

    async def sync_contents_count(self, kb_id: str) -> None:
        """Recompute the denormalized count after an at-least-once document job."""
        result = await self.db.execute(
            select(func.count(Document.id)).where(
                Document.kb_id == kb_id,
                Document.status == Document.STATUS_READY,
            )
        )
        kb = await self.db.get(KnowledgeBase, kb_id)
        if kb:
            kb.contents_count = int(result.scalar() or 0)
            kb.last_updated_at = datetime.utcnow()
            await self.db.commit()
    
    async def increment_view_count(self, kb_id: str):
        """Increment view count."""
        kb = await self.db.get(KnowledgeBase, kb_id)
        if kb:
            kb.view_count += 1
            await self.db.commit()
    
    async def list_public_kbs(
        self,
        category: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[KnowledgeBase], int]:
        """List public knowledge bases (using visibility field)."""
        stmt = select(KnowledgeBase).where(KnowledgeBase.visibility == 'public')
        
        if category:
            stmt = stmt.where(KnowledgeBase.category == category)
        
        if query:
            stmt = stmt.where(
                KnowledgeBase.name.ilike(f"%{query}%") | 
                KnowledgeBase.description.ilike(f"%{query}%")
            )
        
        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar()
        
        # Paginate and order by subscribers
        stmt = stmt.order_by(desc(KnowledgeBase.subscribers_count), desc(KnowledgeBase.created_at))
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        
        result = await self.db.execute(stmt)
        kbs = result.scalars().all()
        
        return list(kbs), total or 0
    
    async def list_featured_kbs(
        self,
        user_id: uuid.UUID,
        user_org_ids: List[uuid.UUID],
        is_admin: bool,
        page: int = 1,
        page_size: int = 30
    ) -> Tuple[List[KnowledgeBase], int]:
        """
        List featured knowledge bases (2025年度精选).
        Admin users: see ALL knowledge bases
        Regular users: see public + organization-shared knowledge bases
        Sorted by: subscribers_count DESC, created_at DESC
        """
        # Base query
        stmt = select(KnowledgeBase)
        
        # Admin users can see ALL knowledge bases, no filtering needed
        if not is_admin:
            # Regular users: apply visibility filtering
            conditions = []
            
            # 1. Public knowledge bases
            conditions.append(KnowledgeBase.visibility == 'public')
            
            # 2. Organization shared KBs that user has access to
            if user_org_ids:
                # Check if any of user's org IDs is in the shared_to_orgs array
                org_conditions = [
                    KnowledgeBase.shared_to_orgs.any(org_id)
                    for org_id in user_org_ids
                ]
                conditions.append(
                    and_(
                        KnowledgeBase.visibility == 'organization',
                        or_(*org_conditions) if org_conditions else False
                    )
                )
            
            # Apply visibility conditions for regular users
            if conditions:
                stmt = stmt.where(or_(*conditions))
        
        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar()
        
        # Order by: subscribers_count DESC, then created_at DESC
        stmt = stmt.order_by(desc(KnowledgeBase.subscribers_count), desc(KnowledgeBase.created_at))
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        
        result = await self.db.execute(stmt)
        kbs = result.scalars().all()
        
        return list(kbs), total or 0
    
    async def get_categories_stats(self) -> List[dict]:
        """Get statistics for each category (only public KBs)."""
        result = await self.db.execute(
            select(
                KnowledgeBase.category,
                func.count(KnowledgeBase.id).label('count'),
                func.sum(KnowledgeBase.subscribers_count).label('subscribers')
            )
            .where(KnowledgeBase.visibility == 'public')
            .group_by(KnowledgeBase.category)
            .order_by(desc('subscribers'))
        )
        
        stats = []
        for row in result.all():
            stats.append({
                "category": row.category,
                "count": row.count,
                "subscribers": row.subscribers or 0
            })
        
        return stats
    
    # === 新增：可见性控制相关方法 ===
    
    async def update_visibility(
        self,
        kb_id: uuid.UUID,
        visibility: str,
        is_admin: bool = False,
        shared_to_orgs: Optional[List[uuid.UUID]] = None,
    ) -> KnowledgeBase:
        """
        Update knowledge base visibility.
        
        Args:
            kb_id: Knowledge base ID
            visibility: Visibility level (private/organization/public)
            is_admin: Whether the requester is admin (only admin can set public)
            shared_to_orgs: Complete organization list for organization visibility
            
        Returns:
            Updated knowledge base
        """
        kb = await self.db.get(KnowledgeBase, kb_id)
        if not kb:
            raise ValueError("Knowledge base not found")
        
        # Only admin can set public
        if visibility == 'public' and not is_admin:
            raise PermissionError("Only administrators can set knowledge bases to public")
        
        if visibility == 'organization':
            if shared_to_orgs is not None:
                if not shared_to_orgs:
                    raise ValueError("Organization visibility requires at least one organization")
                kb.shared_to_orgs = shared_to_orgs
            elif not kb.shared_to_orgs:
                raise ValueError("Organization visibility requires at least one organization")
        else:
            kb.shared_to_orgs = []

        kb.visibility = visibility
        
        await self.db.commit()
        await self.db.refresh(kb)
        return kb
    
    async def share_to_organizations(self, kb_id: uuid.UUID, org_ids: List[uuid.UUID]) -> KnowledgeBase:
        """
        Share knowledge base to specific organizations.
        
        Args:
            kb_id: Knowledge base ID
            org_ids: List of organization IDs
            
        Returns:
            Updated knowledge base
        """
        if not org_ids:
            raise ValueError("Organization visibility requires at least one organization")

        kb = await self.db.get(KnowledgeBase, kb_id)
        if not kb:
            raise ValueError("Knowledge base not found")
        
        logger.info(f"Sharing KB {kb_id} - Current shared_to_orgs: {kb.shared_to_orgs}")
        logger.info(f"New org_ids to share: {org_ids}")
        
        # Merge existing and new org IDs to avoid overwriting
        existing_orgs = set(kb.shared_to_orgs or [])
        new_orgs = set(org_ids)
        merged_orgs = list(existing_orgs.union(new_orgs))
        
        kb.visibility = 'organization'
        kb.shared_to_orgs = merged_orgs
        
        logger.info(f"Updated KB {kb_id} shared_to_orgs: {merged_orgs}")
        
        await self.db.commit()
        await self.db.refresh(kb)
        
        logger.info(f"After commit - KB {kb_id} shared_to_orgs: {kb.shared_to_orgs}")
        
        return kb
    
    async def get_visible_kbs_for_user(
        self,
        user_id: uuid.UUID,
        user_org_ids: List[uuid.UUID],
        is_admin: bool,
        category: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[KnowledgeBase], int]:
        """
        Get knowledge bases visible to a specific user (for plaza).
        
        Visibility rules:
        - Admin users: see ALL knowledge bases (no restrictions)
        - Regular users:
          - Public (admin shared): visible to everyone
          - Organization shared: visible to organization members
          - Private: not visible
        
        Args:
            user_id: User ID
            user_org_ids: List of organization IDs the user belongs to
            is_admin: Whether user is admin
            category: Filter by category
            query: Search query
            page: Page number
            page_size: Page size
            
        Returns:
            Tuple of (knowledge bases, total count)
        """
        # Base query
        stmt = select(KnowledgeBase)
        
        # Admin users can see ALL knowledge bases, no filtering needed
        if not is_admin:
            # Regular users: apply visibility filtering
            conditions = []
            
            # 1. Public knowledge bases
            conditions.append(KnowledgeBase.visibility == 'public')
            
            # 2. Organization shared KBs that user has access to
            if user_org_ids:
                # Check if any of user's org IDs is in the shared_to_orgs array
                org_conditions = [
                    KnowledgeBase.shared_to_orgs.any(org_id)
                    for org_id in user_org_ids
                ]
                conditions.append(
                    and_(
                        KnowledgeBase.visibility == 'organization',
                        or_(*org_conditions) if org_conditions else False
                    )
                )
            
            # Apply visibility conditions for regular users
            if conditions:
                stmt = stmt.where(or_(*conditions))
        
        # Apply additional filters
        if category:
            stmt = stmt.where(KnowledgeBase.category == category)
        
        if query:
            stmt = stmt.where(
                or_(
                    KnowledgeBase.name.ilike(f"%{query}%"),
                    KnowledgeBase.description.ilike(f"%{query}%")
                )
            )
        
        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar()
        
        # Paginate and order
        stmt = stmt.order_by(desc(KnowledgeBase.subscribers_count), desc(KnowledgeBase.created_at))
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        
        result = await self.db.execute(stmt)
        kbs = list(result.scalars().all())
        
        return kbs, total or 0
    
    async def remove_org_from_user_kbs(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID
    ) -> int:
        """
        从用户拥有的所有知识库的 shared_to_orgs 中移除指定组织。
        当用户退出组织时调用。
        
        Args:
            user_id: 用户ID（知识库所有者）
            org_id: 要移除的组织ID
            
        Returns:
            int: 受影响的知识库数量
        """
        # 查找该用户拥有的、共享到该组织的所有知识库
        stmt = select(KnowledgeBase).where(
            and_(
                KnowledgeBase.owner_id == user_id,
                KnowledgeBase.visibility == 'organization',
                KnowledgeBase.shared_to_orgs.any(org_id)
            )
        )
        
        result = await self.db.execute(stmt)
        kbs = list(result.scalars().all())
        
        affected_count = 0
        for kb in kbs:
            # 从 shared_to_orgs 中移除该组织
            new_shared_orgs = [oid for oid in (kb.shared_to_orgs or []) if oid != org_id]
            kb.shared_to_orgs = new_shared_orgs
            
            # 如果没有共享到任何组织了，改为私有
            if not new_shared_orgs:
                kb.visibility = 'private'
                logger.info(f"KB {kb.id} no longer shared to any org, set to private")
            
            affected_count += 1
            logger.info(f"Removed org {org_id} from KB {kb.id} shared_to_orgs")
        
        if affected_count > 0:
            await self.db.commit()
        
        return affected_count
    
    async def remove_org_from_all_kbs(
        self,
        org_id: uuid.UUID
    ) -> int:
        """
        从所有知识库的 shared_to_orgs 中移除指定组织。
        当组织被解散时调用。
        
        Args:
            org_id: 要移除的组织ID
            
        Returns:
            int: 受影响的知识库数量
        """
        # 查找所有共享到该组织的知识库
        stmt = select(KnowledgeBase).where(
            and_(
                KnowledgeBase.visibility == 'organization',
                KnowledgeBase.shared_to_orgs.any(org_id)
            )
        )
        
        result = await self.db.execute(stmt)
        kbs = list(result.scalars().all())
        
        affected_count = 0
        for kb in kbs:
            # 从 shared_to_orgs 中移除该组织
            new_shared_orgs = [oid for oid in (kb.shared_to_orgs or []) if oid != org_id]
            kb.shared_to_orgs = new_shared_orgs
            
            # 如果没有共享到任何组织了，改为私有
            if not new_shared_orgs:
                kb.visibility = 'private'
                logger.info(f"KB {kb.id} no longer shared to any org, set to private")
            
            affected_count += 1
            logger.info(f"Removed org {org_id} from KB {kb.id} shared_to_orgs (org dissolved)")
        
        if affected_count > 0:
            await self.db.commit()
        
        return affected_count
