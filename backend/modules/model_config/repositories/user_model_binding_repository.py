"""Repository for user-scoped model bindings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from modules.model_config.entities.user_model_config import UserModelBinding


class UserModelBindingRepository:
    """Persistence helper for selectable user model bindings."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_user(self, user_id: UUID) -> list[UserModelBinding]:
        result = await self.db.execute(
            select(UserModelBinding)
            .options(joinedload(UserModelBinding.provider_credential))
            .where(UserModelBinding.user_id == user_id)
            .order_by(UserModelBinding.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_by_user_and_binding_name(self, user_id: UUID, binding_name: str) -> UserModelBinding | None:
        result = await self.db.execute(
            select(UserModelBinding)
            .options(joinedload(UserModelBinding.provider_credential))
            .where(
                UserModelBinding.user_id == user_id,
                UserModelBinding.binding_name == binding_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_user(self, binding_id: UUID, user_id: UUID) -> UserModelBinding | None:
        result = await self.db.execute(
            select(UserModelBinding)
            .options(joinedload(UserModelBinding.provider_credential))
            .where(
                UserModelBinding.id == binding_id,
                UserModelBinding.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, binding_id: UUID) -> UserModelBinding | None:
        result = await self.db.execute(
            select(UserModelBinding)
            .options(joinedload(UserModelBinding.provider_credential))
            .where(UserModelBinding.id == binding_id)
        )
        return result.scalar_one_or_none()

    async def create(self, binding: UserModelBinding) -> UserModelBinding:
        self.db.add(binding)
        await self.db.flush()
        await self.db.refresh(binding)
        return binding

    async def delete(self, binding_id: UUID, user_id: UUID) -> bool:
        result = await self.db.execute(
            delete(UserModelBinding).where(
                UserModelBinding.id == binding_id,
                UserModelBinding.user_id == user_id,
            )
        )
        return bool(result.rowcount)

    async def update_enabled_for_user(self, binding_id: UUID, user_id: UUID, *, is_enabled: bool) -> bool:
        result = await self.db.execute(
            update(UserModelBinding)
            .where(
                UserModelBinding.id == binding_id,
                UserModelBinding.user_id == user_id,
            )
            .values(is_enabled=is_enabled)
        )
        return bool(result.rowcount)

    async def update_enabled_for_provider(self, user_id: UUID, provider_code: str, *, is_enabled: bool) -> int:
        result = await self.db.execute(
            update(UserModelBinding)
            .where(
                UserModelBinding.user_id == user_id,
                UserModelBinding.provider_code == provider_code,
            )
            .values(is_enabled=is_enabled)
        )
        return int(result.rowcount or 0)

    async def update_health_status(
        self,
        binding_id: UUID,
        user_id: UUID,
        *,
        health_status: str,
        checked_at: datetime | None,
        latency_ms: int | None,
        error_message: str | None,
    ) -> bool:
        result = await self.db.execute(
            update(UserModelBinding)
            .where(
                UserModelBinding.id == binding_id,
                UserModelBinding.user_id == user_id,
            )
            .values(
                health_status=health_status,
                last_health_checked_at=checked_at,
                last_health_latency_ms=latency_ms,
                last_health_error=error_message,
            )
        )
        return bool(result.rowcount)

    async def clear_health_statuses(self, user_id: UUID) -> int:
        result = await self.db.execute(
            update(UserModelBinding)
            .where(UserModelBinding.user_id == user_id)
            .values(
                health_status="unknown",
                last_health_checked_at=None,
                last_health_latency_ms=None,
                last_health_error=None,
            )
        )
        return int(result.rowcount or 0)
