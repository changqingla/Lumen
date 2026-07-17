"""Application service for quota snapshots and atomic Runtime run admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from config.quota_config import (
    BillingWindow,
    get_billing_window,
    get_effective_user_level,
    get_exceeded_message,
    get_user_model_quota_limit,
)
from config.settings import settings
from repositories.token_usage_repository import TokenUsageRepository
from utils.token_quota_ledger import TokenQuotaLedger
from utils.token_usage_context import UsageContextClaims, create_usage_context


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    user_level: str
    used_tokens: int
    pending_reserved_tokens: int
    quota_limit: int
    reset_date: datetime

    def to_details(self) -> dict[str, object]:
        return {
            "user_level": self.user_level,
            "used_tokens": self.used_tokens,
            "pending_reserved_tokens": self.pending_reserved_tokens,
            "quota_limit": self.quota_limit,
            "reset_date": self.reset_date.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    allowed: bool
    snapshot: QuotaSnapshot
    reservation_id: UUID | None = None
    window_start: datetime | None = None
    reserved_tokens: int = 0
    expires_at: datetime | None = None
    usage_context: str | None = None


class TokenQuotaService:
    """Combine the database total with Redis pending reservations without races."""

    def __init__(self, redis_client, db: AsyncSession):
        self.ledger = TokenQuotaLedger(redis_client)
        self.repository = TokenUsageRepository(db)

    @staticmethod
    def _retention_ttl(window: BillingWindow, now: datetime) -> int:
        until_window_cleanup = int((window.end - now).total_seconds()) + 24 * 60 * 60
        return max(
            until_window_cleanup,
            int(settings.TOKEN_QUOTA_RESERVATION_TTL_SECONDS) + 24 * 60 * 60,
            60,
        )

    async def _database_total(self, user_id: UUID, window: BillingWindow) -> int:
        return await self.repository.get_billing_window_total(user_id, window.start)

    async def get_snapshot(
        self,
        *,
        user,
        now: datetime | None = None,
    ) -> QuotaSnapshot:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        window = get_billing_window(current)
        db_total = await self._database_total(user.id, window)
        ledger_snapshot = await self.ledger.snapshot(
            user_id=user.id,
            window_start=window.start,
            now=current,
            db_committed_tokens=db_total,
            retention_ttl_seconds=self._retention_ttl(window, current),
        )
        return QuotaSnapshot(
            user_level=get_effective_user_level(user, current),
            used_tokens=ledger_snapshot.committed_tokens,
            pending_reserved_tokens=ledger_snapshot.pending_tokens,
            quota_limit=get_user_model_quota_limit(user, current),
            reset_date=window.end,
        )

    async def reserve_run(
        self,
        *,
        user,
        session_id: UUID,
        now: datetime | None = None,
    ) -> QuotaReservation:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        window = get_billing_window(current)
        quota_limit = get_user_model_quota_limit(user, current)
        requested_tokens = int(settings.TOKEN_QUOTA_RUN_RESERVATION_TOKENS)
        reservation_id = uuid4()
        expires_at = current + timedelta(
            seconds=int(settings.TOKEN_QUOTA_RESERVATION_TTL_SECONDS)
        )
        db_total = await self._database_total(user.id, window)

        ledger_result = await self.ledger.reserve(
            user_id=user.id,
            window_start=window.start,
            now=current,
            db_committed_tokens=db_total,
            requested_tokens=requested_tokens,
            quota_limit=quota_limit,
            reservation_id=reservation_id,
            expires_at=expires_at,
            retention_ttl_seconds=self._retention_ttl(window, current),
        )
        snapshot = QuotaSnapshot(
            user_level=get_effective_user_level(user, current),
            used_tokens=ledger_result.committed_tokens,
            pending_reserved_tokens=(
                ledger_result.pending_tokens - requested_tokens
                if ledger_result.allowed
                else ledger_result.pending_tokens
            ),
            quota_limit=quota_limit,
            reset_date=window.end,
        )
        if not ledger_result.allowed:
            return QuotaReservation(allowed=False, snapshot=snapshot)

        claims = UsageContextClaims(
            reservation_id=reservation_id,
            user_id=user.id,
            session_id=session_id,
            window_start=window.start,
            window_end=window.end,
            expires_at=expires_at,
        )
        try:
            usage_context = create_usage_context(claims, issued_at=current)
        except Exception:
            await self.ledger.release_reservation(
                user_id=user.id,
                window_start=window.start,
                reservation_id=reservation_id,
            )
            raise
        return QuotaReservation(
            allowed=True,
            snapshot=snapshot,
            reservation_id=reservation_id,
            window_start=window.start,
            reserved_tokens=requested_tokens,
            expires_at=expires_at,
            usage_context=usage_context,
        )

    async def release(self, reservation: QuotaReservation, *, user_id: UUID) -> int:
        if not reservation.allowed or reservation.reservation_id is None:
            return 0
        if reservation.window_start is None:
            return 0
        return await self.ledger.release_reservation(
            user_id=user_id,
            window_start=reservation.window_start,
            reservation_id=reservation.reservation_id,
        )


def build_quota_exceeded_error(reservation: QuotaReservation) -> dict[str, object]:
    snapshot = reservation.snapshot
    return {
        "code": "QUOTA_EXCEEDED",
        "message": get_exceeded_message(snapshot.user_level),
        "details": snapshot.to_details(),
    }
