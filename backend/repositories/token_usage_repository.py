"""Token usage data access layer."""
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from models.token_usage import TokenUsageRecord


class TokenUsageRepository:
    """Token usage record repository."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_idempotent(self, values: dict) -> bool:
        """Insert an event once across concurrent consumers."""

        statement = (
            insert(TokenUsageRecord)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[TokenUsageRecord.event_id])
            .returning(TokenUsageRecord.event_id)
        )
        result = await self.db.execute(statement)
        await self.db.commit()
        return result.scalar_one_or_none() is not None
    
    async def get_billing_window_total(self, user_id: UUID, window_start: datetime) -> int:
        statement = select(
            func.coalesce(func.sum(TokenUsageRecord.total_tokens), 0)
        ).where(
            TokenUsageRecord.user_id == user_id,
            TokenUsageRecord.billing_window_start == window_start,
        )
        result = await self.db.execute(statement)
        return int(result.scalar_one() or 0)

    async def count_event_ids(self, event_ids: list[UUID]) -> int:
        if not event_ids:
            return 0
        statement = select(func.count(TokenUsageRecord.event_id)).where(
            TokenUsageRecord.event_id.in_(event_ids)
        )
        result = await self.db.execute(statement)
        return int(result.scalar_one() or 0)
