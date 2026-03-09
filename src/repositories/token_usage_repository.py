"""Token usage data access layer."""
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text

from models.token_usage import TokenUsageRecord


class TokenUsageRepository:
    """Token usage record repository."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create(self, record: TokenUsageRecord) -> TokenUsageRecord:
        """Create a new token usage record."""
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record
    
    async def get_by_id(self, record_id: UUID) -> Optional[TokenUsageRecord]:
        """Get a token usage record by ID."""
        stmt = select(TokenUsageRecord).where(TokenUsageRecord.id == record_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_user_usage_hourly(
        self,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime
    ) -> List[dict]:
        """
        Get user's token usage aggregated by hour.
        
        Returns list of dicts with: time, input_tokens, output_tokens
        """
        stmt = text("""
            SELECT 
                date_trunc('hour', created_at) as time,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM token_usage_records
            WHERE user_id = :user_id 
              AND created_at >= :start_time 
              AND created_at < :end_time
            GROUP BY date_trunc('hour', created_at)
            ORDER BY time
        """)
        
        result = await self.db.execute(stmt, {
            "user_id": str(user_id),
            "start_time": start_time,
            "end_time": end_time
        })
        
        return [
            {
                "time": row.time.isoformat() if row.time else None,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0
            }
            for row in result.fetchall()
        ]
    
    async def get_user_usage_daily(
        self,
        user_id: UUID,
        start_time: datetime,
        end_time: datetime
    ) -> List[dict]:
        """
        Get user's token usage aggregated by day.
        
        Returns list of dicts with: time, input_tokens, output_tokens
        """
        stmt = text("""
            SELECT 
                date_trunc('day', created_at) as time,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM token_usage_records
            WHERE user_id = :user_id 
              AND created_at >= :start_time 
              AND created_at < :end_time
            GROUP BY date_trunc('day', created_at)
            ORDER BY time
        """)
        
        result = await self.db.execute(stmt, {
            "user_id": str(user_id),
            "start_time": start_time,
            "end_time": end_time
        })
        
        return [
            {
                "time": row.time.isoformat() if row.time else None,
                "input_tokens": row.input_tokens or 0,
                "output_tokens": row.output_tokens or 0
            }
            for row in result.fetchall()
        ]
    
    async def get_user_total(
        self,
        user_id: UUID,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> dict:
        """
        Get user's total token usage within time range.
        
        Returns dict with: input_tokens, output_tokens, total_tokens, request_count
        """
        conditions = [TokenUsageRecord.user_id == user_id]
        if start_time:
            conditions.append(TokenUsageRecord.created_at >= start_time)
        if end_time:
            conditions.append(TokenUsageRecord.created_at < end_time)
        
        stmt = select(
            func.sum(TokenUsageRecord.input_tokens).label('input_tokens'),
            func.sum(TokenUsageRecord.output_tokens).label('output_tokens'),
            func.sum(TokenUsageRecord.total_tokens).label('total_tokens'),
            func.count(TokenUsageRecord.id).label('request_count')
        ).where(*conditions)
        
        result = await self.db.execute(stmt)
        row = result.one()
        
        return {
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "request_count": row.request_count or 0
        }
