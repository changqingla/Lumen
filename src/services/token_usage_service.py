"""Token usage service for tracking LLM API consumption."""
import logging
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from models.token_usage import TokenUsageRecord
from repositories.token_usage_repository import TokenUsageRepository

logger = logging.getLogger(__name__)


class TokenUsageService:
    """Service for managing token usage records."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = TokenUsageRepository(db)
    
    async def record_usage(
        self,
        user_id: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        session_id: Optional[str] = None,
        request_type: Optional[str] = None
    ) -> Optional[TokenUsageRecord]:
        """
        Record token usage for a request.
        
        Args:
            user_id: User ID
            model_name: LLM model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            session_id: Optional session ID
            request_type: Optional request type (chat/summary/comparison)
            
        Returns:
            Created record or None if validation fails
        """
        # Validate non-negative tokens
        if input_tokens < 0 or output_tokens < 0:
            logger.warning(
                f"Invalid token counts: input={input_tokens}, output={output_tokens}. "
                "Token counts must be non-negative."
            )
            return None
        
        # Validate user_id
        if not user_id:
            logger.warning("Cannot record token usage: user_id is required")
            return None
        
        try:
            record = TokenUsageRecord(
                user_id=UUID(user_id),
                session_id=session_id,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                request_type=request_type
            )
            return await self.repository.create(record)
        except Exception as e:
            logger.error(f"Failed to record token usage: {e}")
            return None
    
    async def get_hourly_stats(
        self,
        user_id: str,
        hours: int = 24,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> dict:
        """
        Get user's token usage statistics aggregated by hour.
        
        Args:
            user_id: User ID
            hours: Number of hours to look back (default 24, ignored if custom range provided)
            start_time: Optional custom start time
            end_time: Optional custom end time
            
        Returns:
            Dict with period, data (hourly buckets), and total
        """
        # Use custom range if provided, otherwise calculate from hours
        if start_time and end_time:
            actual_start = start_time
            actual_end = end_time
        else:
            actual_end = datetime.now(timezone.utc)
            actual_start = actual_end - timedelta(hours=hours)
        
        data = await self.repository.get_user_usage_hourly(
            UUID(user_id), actual_start, actual_end
        )
        total = await self.repository.get_user_total(
            UUID(user_id), actual_start, actual_end
        )
        
        return {
            "period": "hourly",
            "start_time": actual_start.isoformat(),
            "end_time": actual_end.isoformat(),
            "data": data,
            "total": total
        }
    
    async def get_daily_stats(
        self,
        user_id: str,
        days: int = 30,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> dict:
        """
        Get user's token usage statistics aggregated by day.
        
        Args:
            user_id: User ID
            days: Number of days to look back (default 30, ignored if custom range provided)
            start_time: Optional custom start time
            end_time: Optional custom end time
            
        Returns:
            Dict with period, data (daily buckets), and total
        """
        # Use custom range if provided, otherwise calculate from days
        if start_time and end_time:
            actual_start = start_time
            actual_end = end_time
        else:
            actual_end = datetime.now(timezone.utc)
            actual_start = actual_end - timedelta(days=days)
        
        data = await self.repository.get_user_usage_daily(
            UUID(user_id), actual_start, actual_end
        )
        total = await self.repository.get_user_total(
            UUID(user_id), actual_start, actual_end
        )
        
        return {
            "period": "daily",
            "start_time": actual_start.isoformat(),
            "end_time": actual_end.isoformat(),
            "data": data,
            "total": total
        }
    
    async def get_total_usage(
        self,
        user_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> dict:
        """
        Get user's total token usage.
        
        Args:
            user_id: User ID
            start_time: Optional start time filter
            end_time: Optional end time filter
            
        Returns:
            Dict with input_tokens, output_tokens, total_tokens, request_count
        """
        return await self.repository.get_user_total(
            UUID(user_id), start_time, end_time
        )
