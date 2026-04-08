"""Repository for user-scoped provider credentials."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.model_config.entities.user_model_config import UserModelProviderCredential


class UserModelProviderRepository:
    """Persistence helper for provider credentials."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_user_and_provider(self, user_id: UUID, provider_code: str) -> UserModelProviderCredential | None:
        result = await self.db.execute(
            select(UserModelProviderCredential).where(
                UserModelProviderCredential.user_id == user_id,
                UserModelProviderCredential.provider_code == provider_code,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: UUID) -> list[UserModelProviderCredential]:
        result = await self.db.execute(
            select(UserModelProviderCredential).where(
                UserModelProviderCredential.user_id == user_id,
            )
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        user_id: UUID,
        provider_code: str,
        custom_base_url: str | None = None,
        api_key_encrypted: str,
        api_key_masked: str,
    ) -> UserModelProviderCredential:
        credential = UserModelProviderCredential(
            user_id=user_id,
            provider_code=provider_code,
            custom_base_url=custom_base_url,
            api_key_encrypted=api_key_encrypted,
            api_key_masked=api_key_masked,
        )
        self.db.add(credential)
        await self.db.flush()
        await self.db.refresh(credential)
        return credential

    async def update(
        self,
        credential: UserModelProviderCredential,
        *,
        custom_base_url: str | None = None,
        api_key_encrypted: str,
        api_key_masked: str,
    ) -> UserModelProviderCredential:
        credential.custom_base_url = custom_base_url
        credential.api_key_encrypted = api_key_encrypted
        credential.api_key_masked = api_key_masked
        credential.is_active = True
        await self.db.flush()
        await self.db.refresh(credential)
        return credential

    async def delete_by_user_and_provider(self, user_id: UUID, provider_code: str) -> bool:
        result = await self.db.execute(
            delete(UserModelProviderCredential).where(
                UserModelProviderCredential.user_id == user_id,
                UserModelProviderCredential.provider_code == provider_code,
            )
        )
        return bool(result.rowcount)
