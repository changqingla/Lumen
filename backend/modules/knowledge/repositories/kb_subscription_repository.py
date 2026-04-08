"""Knowledge Base Subscription repository."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from typing import Optional, List, Tuple
from modules.knowledge.entities.knowledge_base import KnowledgeBaseSubscription, KnowledgeBase
from sqlalchemy.orm import selectinload
from datetime import datetime


class KBSubscriptionRepository:
    """Repository for Knowledge Base subscriptions."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def subscribe(self, user_id: str, kb_id: str) -> KnowledgeBaseSubscription:
        """Subscribe to a knowledge base."""
        # Check if already subscribed
        existing = await self.get_subscription(user_id, kb_id)
        if existing:
            return existing
        
        subscription = KnowledgeBaseSubscription(
            user_id=user_id,
            kb_id=kb_id
        )
        self.db.add(subscription)
        
        # Increment subscribers count
        kb = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        kb_obj = kb.scalar_one_or_none()
        if kb_obj:
            kb_obj.subscribers_count += 1
        
        await self.db.commit()
        await self.db.refresh(subscription)
        return subscription
    
    async def unsubscribe(self, user_id: str, kb_id: str) -> bool:
        """Unsubscribe from a knowledge base."""
        result = await self.db.execute(
            delete(KnowledgeBaseSubscription).where(
                KnowledgeBaseSubscription.user_id == user_id,
                KnowledgeBaseSubscription.kb_id == kb_id
            )
        )
        
        if result.rowcount > 0:
            # Decrement subscribers count
            kb = await self.db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            )
            kb_obj = kb.scalar_one_or_none()
            if kb_obj and kb_obj.subscribers_count > 0:
                kb_obj.subscribers_count -= 1
            
            await self.db.commit()
            return True
        return False
    
    async def get_subscription(
        self, 
        user_id: str, 
        kb_id: str
    ) -> Optional[KnowledgeBaseSubscription]:
        """Check if user is subscribed to a knowledge base."""
        result = await self.db.execute(
            select(KnowledgeBaseSubscription).where(
                KnowledgeBaseSubscription.user_id == user_id,
                KnowledgeBaseSubscription.kb_id == kb_id
            )
        )
        return result.scalar_one_or_none()
    
    async def list_user_subscriptions(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[KnowledgeBase], int]:
        """List all knowledge bases subscribed by a user."""
        # Count total
        count_stmt = select(func.count()).select_from(
            select(KnowledgeBaseSubscription)
            .where(KnowledgeBaseSubscription.user_id == user_id)
            .subquery()
        )
        total = (await self.db.execute(count_stmt)).scalar()
        
        # Get subscribed KBs
        stmt = (
            select(KnowledgeBase)
            .join(
                KnowledgeBaseSubscription,
                KnowledgeBase.id == KnowledgeBaseSubscription.kb_id
            )
            .where(KnowledgeBaseSubscription.user_id == user_id)
            .order_by(KnowledgeBaseSubscription.subscribed_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        
        result = await self.db.execute(stmt)
        kbs = result.scalars().all()
        
        return list(kbs), total or 0
    
    async def update_last_viewed(self, user_id: str, kb_id: str):
        """Update last viewed timestamp."""
        subscription = await self.get_subscription(user_id, kb_id)
        if subscription:
            subscription.last_viewed_at = datetime.utcnow()
            await self.db.commit()
