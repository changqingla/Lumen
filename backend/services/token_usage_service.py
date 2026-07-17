"""Transactional persistence for trusted Runtime token usage events."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from repositories.token_usage_repository import TokenUsageRepository
from schemas.token_usage import RuntimeTokenUsageEvent
from utils.token_usage_context import UsageContextClaims


class TokenUsageService:
    """Commit idempotent events before Redis reservation state is settled."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = TokenUsageRepository(db)

    async def record_runtime_event(
        self,
        *,
        claims: UsageContextClaims,
        event: RuntimeTokenUsageEvent,
    ) -> int:
        """Commit one trusted Runtime event and return the window's DB total."""

        if event.kind != "usage":
            raise ValueError("Only usage events can be persisted")
        assert event.model_name is not None
        assert event.input_tokens is not None
        assert event.output_tokens is not None
        assert event.total_tokens is not None
        assert event.request_type is not None
        assert event.usage_source is not None

        try:
            await self.repository.create_idempotent(
                {
                    "event_id": event.event_id,
                    "reservation_id": claims.reservation_id,
                    "user_id": claims.user_id,
                    "session_id": str(claims.session_id),
                    "model_name": event.model_name,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "total_tokens": event.total_tokens,
                    "request_type": event.request_type,
                    "usage_source": event.usage_source,
                    "occurred_at": event.occurred_at,
                    "billing_window_start": claims.window_start,
                }
            )
        except Exception:
            await self.db.rollback()
            raise
        return await self.get_billing_window_total(claims)

    async def get_billing_window_total(self, claims: UsageContextClaims) -> int:
        return await self.repository.get_billing_window_total(
            claims.user_id,
            claims.window_start,
        )

    async def all_events_committed(self, event_ids: list[UUID]) -> bool:
        return await self.repository.count_event_ids(event_ids) == len(event_ids)
