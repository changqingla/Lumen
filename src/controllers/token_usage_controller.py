"""Token usage API controller."""
import logging
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from services.token_usage_service import TokenUsageService
from services.quota_service import QuotaService
from middlewares.auth import get_current_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/token-usage", tags=["token-usage"])


@router.get("/hourly")
async def get_hourly_usage(
    hours: int = Query(default=24, ge=1, le=168, description="Hours to look back (max 168 = 7 days)"),
    start_time: Optional[datetime] = Query(default=None, description="Custom start time (ISO format)"),
    end_time: Optional[datetime] = Query(default=None, description="Custom end time (ISO format)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's token usage aggregated by hour.
    
    Returns hourly buckets with input_tokens, output_tokens for chart visualization.
    Supports optional custom date range via start_time and end_time parameters.
    """
    # Validate date range if provided
    if start_time and end_time and start_time >= end_time:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_DATE_RANGE", "message": "start_time must be before end_time"}}
        )
    
    service = TokenUsageService(db)
    return await service.get_hourly_stats(
        str(current_user.id), 
        hours,
        start_time=start_time,
        end_time=end_time
    )


@router.get("/daily")
async def get_daily_usage(
    days: int = Query(default=30, ge=1, le=365, description="Days to look back (max 365)"),
    start_time: Optional[datetime] = Query(default=None, description="Custom start time (ISO format)"),
    end_time: Optional[datetime] = Query(default=None, description="Custom end time (ISO format)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's token usage aggregated by day.
    
    Returns daily buckets with input_tokens, output_tokens for chart visualization.
    Supports optional custom date range via start_time and end_time parameters.
    """
    # Validate date range if provided
    if start_time and end_time and start_time >= end_time:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_DATE_RANGE", "message": "start_time must be before end_time"}}
        )
    
    service = TokenUsageService(db)
    return await service.get_daily_stats(
        str(current_user.id), 
        days,
        start_time=start_time,
        end_time=end_time
    )


@router.get("/total")
async def get_total_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's total token usage (all time).
    
    Returns total input_tokens, output_tokens, total_tokens, and request_count.
    """
    service = TokenUsageService(db)
    return await service.get_total_usage(str(current_user.id))


@router.get("/quota-status")
async def get_quota_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取当前用户的配额状态
    
    Returns:
        - user_level: 用户等级 (basic/member/premium)
        - quota_limit: 配额上限
        - used_tokens: 已使用 tokens
        - remaining_tokens: 剩余 tokens
        - is_exceeded: 是否超限
        - billing_cycle_start: 计费周期开始时间
        - billing_cycle_end: 计费周期结束时间
        - reset_date: 配额重置时间
    """
    quota_service = QuotaService(db)
    status = await quota_service.check_quota(current_user)
    
    return {
        "user_level": status.user_level,
        "quota_limit": status.quota_limit,
        "used_tokens": status.used_tokens,
        "remaining_tokens": status.remaining_tokens,
        "is_exceeded": status.is_exceeded,
        "billing_cycle_start": status.billing_cycle_start.isoformat(),
        "billing_cycle_end": status.billing_cycle_end.isoformat(),
        "reset_date": status.billing_cycle_end.isoformat()
    }
