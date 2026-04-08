"""Provider registry and user model configuration services."""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from modules.model_config.registry.model_provider_registry import (
    ProviderDefinition,
    ProviderModelDefinition,
    find_provider_for_runtime_model,
    get_provider_definition,
    list_provider_definitions,
)
from modules.model_config.entities.user_model_config import UserModelBinding
from modules.model_config.repositories.user_model_binding_repository import UserModelBindingRepository
from modules.model_config.repositories.user_model_provider_repository import UserModelProviderRepository
from utils.external_services import get_http_client
from modules.model_config.security.model_config_security import (
    create_runtime_model_binding_token,
    decode_runtime_model_binding_token,
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)

logger = logging.getLogger(__name__)


def build_user_model_binding_name(binding_id: UUID | str) -> str:
    return f"user-model:{binding_id}"


class ModelConfigService:
    """Main application service for provider registry and user model bindings."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider_repo = UserModelProviderRepository(db)
        self.binding_repo = UserModelBindingRepository(db)

    def list_provider_catalog(
        self,
        *,
        provider_credentials: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        providers: list[dict[str, Any]] = []
        for provider in list_provider_definitions():
            credential = provider_credentials.get(provider.code, {})
            providers.append(
                {
                    "code": provider.code,
                    "display_name": provider.display_name,
                    "description": provider.description,
                    "icon_key": provider.icon_key,
                    "api_key_label": provider.api_key_label,
                    "base_url": str(credential.get("base_url") or provider.base_url or ""),
                    "credential_configured": bool(credential.get("credential_configured", False)),
                    "api_key_masked": credential.get("api_key_masked"),
                    "models": [self._serialize_provider_model(provider, model) for model in provider.models],
                }
            )
        return providers

    async def list_provider_catalog_for_user(self, user_id: UUID) -> list[dict[str, Any]]:
        provider_credentials = await self._build_provider_credentials_map(user_id)
        return self.list_provider_catalog(provider_credentials=provider_credentials)

    def serialize_user_model_binding(self, binding: UserModelBinding) -> dict[str, Any]:
        provider = get_provider_definition(binding.provider_code)
        return {
            "id": str(binding.id),
            "name": binding.binding_name,
            "display_name": binding.display_name,
            "description": binding.description,
            "provider_code": binding.provider_code,
            "provider_display_name": provider.display_name if provider else binding.provider_code,
            "provider_icon_key": provider.icon_key if provider else "system",
            "provider_model_name": binding.provider_model_name,
            "supports_vision": binding.supports_vision,
            "supports_thinking": binding.supports_thinking,
            "supports_reasoning_effort": binding.supports_reasoning_effort,
            "is_enabled": binding.is_enabled,
            "health_status": str(getattr(binding, "health_status", "unknown") or "unknown"),
            "last_health_checked_at": (
                binding.last_health_checked_at.isoformat()
                if getattr(binding, "last_health_checked_at", None) is not None
                else None
            ),
            "last_health_latency_ms": getattr(binding, "last_health_latency_ms", None),
            "last_health_error": getattr(binding, "last_health_error", None),
            "source": "user",
        }

    def serialize_system_model(self, runtime_model: dict[str, Any]) -> dict[str, Any]:
        name = str(runtime_model.get("name") or "").strip()
        display_name = str(runtime_model.get("display_name") or name).strip() or name
        description = str(runtime_model.get("description") or "").strip() or None
        matched = find_provider_for_runtime_model(name)
        provider = matched[0] if matched else None
        model_meta = matched[1] if matched else None
        return {
            "name": name,
            "display_name": display_name,
            "description": description or (model_meta.description if model_meta and model_meta.description else None),
            "supports_vision": bool(runtime_model.get("supports_vision", False)),
            "supports_thinking": bool(runtime_model.get("supports_thinking", False)),
            "supports_reasoning_effort": bool(runtime_model.get("supports_reasoning_effort", False)),
            "provider_code": provider.code if provider else "system",
            "provider_display_name": provider.display_name if provider else "系统默认",
            "provider_icon_key": provider.icon_key if provider else "system",
            "source": "system",
        }

    async def list_user_model_bindings(self, user_id: UUID) -> list[dict[str, Any]]:
        bindings = await self.binding_repo.list_by_user(user_id)
        return [self.serialize_user_model_binding(binding) for binding in bindings if binding.is_enabled]

    async def list_remote_provider_models(self, user_id: UUID, provider_code: str) -> dict[str, Any]:
        provider = self._require_provider(provider_code)
        credential = await self.provider_repo.get_by_user_and_provider(user_id, provider.code)
        if credential is None or not credential.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请先保存该供应商的 API Key",
            )

        try:
            api_key = decrypt_api_key(credential.api_key_encrypted)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        base_url = self._resolve_provider_base_url(provider, credential)
        models = await self._fetch_remote_provider_models(provider, api_key, base_url=base_url)
        return {
            "provider_code": provider.code,
            "provider_display_name": provider.display_name,
            "provider_icon_key": provider.icon_key,
            "base_url": base_url,
            "models": models,
        }

    async def preview_remote_provider_models(
        self,
        user_id: UUID,
        provider_code: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_code)
        credential, effective_api_key, resolved_base_url = await self._resolve_provider_access(
            user_id,
            provider,
            api_key=api_key,
            base_url=base_url,
        )
        models = await self._fetch_remote_provider_models(provider, effective_api_key, base_url=resolved_base_url)
        return {
            "provider_code": provider.code,
            "provider_display_name": provider.display_name,
            "provider_icon_key": provider.icon_key,
            "base_url": resolved_base_url,
            "models": models,
        }

    async def save_provider_credential(
        self,
        user_id: UUID,
        provider_code: str,
        api_key: str | None,
        *,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_code)
        resolved_base_url = self._resolve_requested_base_url(provider, base_url)
        try:
            existing = await self.provider_repo.get_by_user_and_provider(user_id, provider.code)
            normalized_api_key = str(api_key or "").strip()
            if normalized_api_key:
                encrypted = encrypt_api_key(normalized_api_key)
                masked = mask_api_key(normalized_api_key)
            elif existing is None or not existing.is_active:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先填写该供应商的 API Key")
            else:
                encrypted = existing.api_key_encrypted
                masked = existing.api_key_masked

            if existing is None:
                await self.provider_repo.create(
                    user_id=user_id,
                    provider_code=provider.code,
                    custom_base_url=resolved_base_url if provider.code == "custom" else None,
                    api_key_encrypted=encrypted,
                    api_key_masked=masked,
                )
            else:
                await self.provider_repo.update(
                    existing,
                    custom_base_url=resolved_base_url if provider.code == "custom" else None,
                    api_key_encrypted=encrypted,
                    api_key_masked=masked,
                )
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            self._raise_integrity_error(exc)
        except Exception:
            await self.db.rollback()
            raise

        return {
            "provider_code": provider.code,
            "provider_display_name": provider.display_name,
            "provider_icon_key": provider.icon_key,
            "api_key_masked": masked,
            "credential_configured": True,
        }

    async def create_model_binding(
        self,
        user_id: UUID,
        provider_code: str,
        provider_model_name: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        provider = self._require_provider(provider_code)
        normalized_api_key = str(api_key or "").strip()
        credential, effective_api_key, resolved_base_url = await self._resolve_provider_access(
            user_id,
            provider,
            api_key=api_key,
            base_url=base_url,
        )
        remote_models = await self._fetch_remote_provider_models(provider, effective_api_key, base_url=resolved_base_url)
        model_metadata = self._find_remote_provider_model(remote_models, provider_model_name)

        try:
            if normalized_api_key:
                encrypted = encrypt_api_key(normalized_api_key)
                masked = mask_api_key(normalized_api_key)
                if credential is None:
                    credential = await self.provider_repo.create(
                        user_id=user_id,
                        provider_code=provider.code,
                        custom_base_url=resolved_base_url if provider.code == "custom" else None,
                        api_key_encrypted=encrypted,
                        api_key_masked=masked,
                    )
                else:
                    credential = await self.provider_repo.update(
                        credential,
                        custom_base_url=resolved_base_url if provider.code == "custom" else None,
                        api_key_encrypted=encrypted,
                        api_key_masked=masked,
                    )
            elif credential is not None and credential.is_active and provider.code == "custom":
                current_base_url = self._resolve_provider_base_url(provider, credential)
                if resolved_base_url != current_base_url:
                    credential = await self.provider_repo.update(
                        credential,
                        custom_base_url=resolved_base_url,
                        api_key_encrypted=credential.api_key_encrypted,
                        api_key_masked=credential.api_key_masked,
                    )

            if credential is None or not credential.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="请先填写该供应商的 API Key",
                )

            existing_bindings = await self.binding_repo.list_by_user(user_id)
            for existing in existing_bindings:
                if existing.provider_code == provider.code and existing.provider_model_name == model_metadata["name"]:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="该模型已添加",
                    )

            binding_id = uuid.uuid4()
            binding = UserModelBinding(
                id=binding_id,
                user_id=user_id,
                provider_credential_id=credential.id,
                provider_code=provider.code,
                binding_name=build_user_model_binding_name(binding_id),
                provider_model_name=model_metadata["name"],
                display_name=str(model_metadata.get("display_name") or model_metadata["name"]).strip(),
                description=model_metadata["description"] or None,
                supports_vision=bool(model_metadata["supports_vision"]),
                supports_thinking=bool(model_metadata["supports_thinking"]),
                supports_reasoning_effort=bool(model_metadata["supports_reasoning_effort"]),
                is_enabled=True,
            )
            created = await self.binding_repo.create(binding)
            await self.db.commit()
        except HTTPException:
            await self.db.rollback()
            raise
        except IntegrityError as exc:
            await self.db.rollback()
            self._raise_integrity_error(exc)
        except Exception:
            await self.db.rollback()
            raise

        created = await self.binding_repo.get_by_id(created.id) or created
        return self.serialize_user_model_binding(created)

    async def update_model_binding_enabled(
        self,
        user_id: UUID,
        binding_id: UUID,
        *,
        is_enabled: bool,
    ) -> dict[str, Any]:
        try:
            updated = await self.binding_repo.update_enabled_for_user(binding_id, user_id, is_enabled=is_enabled)
            if not updated:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型绑定不存在")
            await self.db.commit()
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception:
            await self.db.rollback()
            raise

        binding = await self.binding_repo.get_by_id_for_user(binding_id, user_id)
        if binding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型绑定不存在")
        return self.serialize_user_model_binding(binding)

    async def update_provider_bindings_enabled(
        self,
        user_id: UUID,
        provider_code: str,
        *,
        is_enabled: bool,
    ) -> list[dict[str, Any]]:
        provider = self._require_provider(provider_code)
        try:
            updated_count = await self.binding_repo.update_enabled_for_provider(user_id, provider.code, is_enabled=is_enabled)
            if updated_count == 0:
                bindings = await self.binding_repo.list_by_user(user_id)
                if not any(binding.provider_code == provider.code for binding in bindings):
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该供应商下没有已添加模型")
            await self.db.commit()
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception:
            await self.db.rollback()
            raise

        bindings = await self.binding_repo.list_by_user(user_id)
        return [
            self.serialize_user_model_binding(binding)
            for binding in bindings
            if binding.provider_code == provider.code
        ]

    async def run_model_binding_health_check(self, user_id: UUID, binding_id: UUID) -> dict[str, Any]:
        binding = await self.binding_repo.get_by_id_for_user(binding_id, user_id)
        if binding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型绑定不存在")
        if binding.provider_credential is None or not binding.provider_credential.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型供应商凭据不可用")

        provider = self._require_provider(binding.provider_code)
        try:
            api_key = decrypt_api_key(binding.provider_credential.api_key_encrypted)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        started_at = perf_counter()
        checked_at = datetime.now(timezone.utc)
        health_status = "healthy"
        latency_ms: int | None = None
        error_message: str | None = None

        try:
            await self._run_provider_model_health_check(
                provider,
                binding.provider_model_name,
                api_key,
                base_url=self._resolve_provider_base_url(provider, binding.provider_credential),
            )
            latency_ms = max(1, int((perf_counter() - started_at) * 1000))
        except HTTPException as exc:
            health_status = "unhealthy"
            latency_ms = max(1, int((perf_counter() - started_at) * 1000))
            error_message = str(exc.detail or "模型健康检测失败")
        except Exception:
            health_status = "unhealthy"
            latency_ms = max(1, int((perf_counter() - started_at) * 1000))
            error_message = "模型健康检测失败"

        try:
            await self.binding_repo.update_health_status(
                binding.id,
                user_id,
                health_status=health_status,
                checked_at=checked_at,
                latency_ms=latency_ms,
                error_message=error_message,
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        refreshed = await self.binding_repo.get_by_id_for_user(binding.id, user_id)
        if refreshed is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型绑定不存在")
        return self.serialize_user_model_binding(refreshed)

    async def clear_model_health_statuses(self, user_id: UUID) -> dict[str, Any]:
        try:
            cleared_count = await self.binding_repo.clear_health_statuses(user_id)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
        return {
            "success": True,
            "cleared_count": cleared_count,
        }

    async def delete_model_binding(self, user_id: UUID, binding_id: UUID) -> None:
        try:
            deleted = await self.binding_repo.delete(binding_id, user_id)
            if not deleted:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型绑定不存在")
            await self.db.commit()
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception:
            await self.db.rollback()
            raise

    async def delete_provider_credential(self, user_id: UUID, provider_code: str) -> dict[str, Any]:
        provider = self._require_provider(provider_code)
        bindings = await self.binding_repo.list_by_user(user_id)
        removed_bindings_count = sum(1 for binding in bindings if binding.provider_code == provider.code)
        try:
            deleted = await self.provider_repo.delete_by_user_and_provider(user_id, provider.code)
            if not deleted:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型供应商凭据不存在")
            await self.db.commit()
        except HTTPException:
            await self.db.rollback()
            raise
        except Exception:
            await self.db.rollback()
            raise

        return {
            "provider_code": provider.code,
            "provider_display_name": provider.display_name,
            "removed_bindings_count": removed_bindings_count,
            "success": True,
        }

    async def resolve_selected_model(
        self,
        *,
        user_id: UUID,
        selected_model_name: str | None,
        runtime_models: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(selected_model_name or "").strip()
        if not normalized:
            if runtime_models:
                first = runtime_models[0]
                return {
                    "kind": "system",
                    "runtime_model_name": str(first.get("name") or "").strip(),
                    "dynamic_model_token": None,
                }
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="当前没有可用模型")

        runtime_model_names = {
            str(item.get("name") or "").strip()
            for item in runtime_models
            if str(item.get("name") or "").strip()
        }
        if normalized in runtime_model_names:
            return {
                "kind": "system",
                "runtime_model_name": normalized,
                "dynamic_model_token": None,
            }

        binding = await self.binding_repo.get_by_user_and_binding_name(user_id, normalized)
        if binding is None or not binding.is_enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="所选模型不存在或不可用")

        return {
            "kind": "user",
            "runtime_model_name": binding.binding_name,
            "dynamic_model_token": create_runtime_model_binding_token(
                binding_id=str(binding.id),
                user_id=str(user_id),
                thread_id=thread_id,
            ),
        }

    async def resolve_runtime_binding(self, token: str, *, thread_id: str | None = None) -> dict[str, Any]:
        try:
            payload = decode_runtime_model_binding_token(token)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        binding_id = payload.get("binding_id")
        user_id = payload.get("user_id")
        token_thread_id = str(payload.get("thread_id") or "").strip()
        if not binding_id or not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型绑定令牌缺少必要字段")
        if token_thread_id:
            normalized_thread_id = str(thread_id or "").strip()
            if not normalized_thread_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型绑定令牌缺少线程上下文")
            if normalized_thread_id != token_thread_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型绑定令牌与当前线程不匹配")

        binding_uuid, user_uuid = self._parse_runtime_binding_identity(binding_id, user_id)
        binding = await self.binding_repo.get_by_id_for_user(binding_uuid, user_uuid)
        if binding is None or not binding.is_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型绑定不存在或已禁用")
        if binding.provider_credential is None or not binding.provider_credential.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型供应商凭据不可用")

        provider = self._require_provider(binding.provider_code)
        try:
            api_key = decrypt_api_key(binding.provider_credential.api_key_encrypted)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        resolved_config = {
            **provider.default_settings,
            "model": binding.provider_model_name,
            "api_key": api_key,
        }
        resolved_base_url = self._resolve_provider_base_url(provider, binding.provider_credential)
        if resolved_base_url:
            resolved_config["base_url"] = resolved_base_url

        return {
            "name": binding.binding_name,
            "display_name": binding.display_name,
            "description": binding.description,
            "use": provider.use,
            "supports_vision": binding.supports_vision,
            "supports_thinking": binding.supports_thinking,
            "supports_reasoning_effort": binding.supports_reasoning_effort,
            "config": resolved_config,
        }

    async def list_model_config_page(self, user_id: UUID) -> dict[str, Any]:
        bindings = await self.binding_repo.list_by_user(user_id)
        provider_credentials = await self._build_provider_credentials_map(user_id)
        return {
            "providers": self.list_provider_catalog(provider_credentials=provider_credentials),
            "user_models": [
                self.serialize_user_model_binding(binding)
                for binding in bindings
            ],
        }

    async def _fetch_remote_provider_models(
        self,
        provider: ProviderDefinition,
        api_key: str,
        *,
        base_url: str | None = None,
    ) -> list[dict[str, Any]]:
        if provider.code in {"minimax", "dashscope-coding"}:
            await self._probe_openai_compatible_provider(provider, api_key, base_url=base_url)
            return [self._serialize_provider_model(provider, model) for model in provider.models]

        client = get_http_client()
        resolved_base_url = str(base_url or provider.base_url or "").strip()
        if not resolved_base_url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先填写 Base URL")
        url = f"{resolved_base_url.rstrip('/')}{provider.models_api_path}"

        try:
            if provider.remote_models_format == "anthropic":
                response = await client.get(
                    url,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
            elif provider.remote_models_format == "gemini":
                response = await client.get(
                    url,
                    params={"key": api_key},
                )
            else:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = "拉取供应商模型列表失败"
            if exc.response.status_code in {401, 403}:
                detail = "API Key 无效或没有获取模型列表的权限"
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
        except httpx.HTTPError as exc:
            logger.exception("Failed to fetch remote provider models for '%s'", provider.code)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="连接模型供应商失败，请稍后重试",
            ) from exc

        payload = response.json()
        remote_models = self._parse_remote_provider_models(provider, payload)
        if not remote_models:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="当前供应商未返回可用模型")
        return remote_models

    def _parse_remote_provider_models(
        self,
        provider: ProviderDefinition,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if provider.remote_models_format == "anthropic":
            raw_models = payload.get("data")
        elif provider.remote_models_format == "gemini":
            raw_models = payload.get("models")
        else:
            raw_models = payload.get("data")

        if not isinstance(raw_models, list):
            return []

        static_models = {item.name: item for item in provider.models}
        parsed_models: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for item in raw_models:
            if not isinstance(item, dict):
                continue

            if provider.remote_models_format == "gemini":
                raw_name = str(item.get("name") or "").strip()
                model_name = raw_name.removeprefix("models/").strip()
                supported_methods = item.get("supportedGenerationMethods")
                if isinstance(supported_methods, list) and supported_methods:
                    if not any(
                        str(method or "").strip().lower() in {"generatecontent", "streamgeneratecontent"}
                        for method in supported_methods
                    ):
                        continue
                display_name = str(item.get("displayName") or model_name).strip()
                description = str(item.get("description") or "").strip() or None
            elif provider.remote_models_format == "anthropic":
                model_name = str(item.get("id") or "").strip()
                display_name = str(item.get("display_name") or item.get("name") or model_name).strip()
                description = str(item.get("description") or "").strip() or None
            else:
                model_name = str(item.get("id") or "").strip()
                display_name = str(item.get("display_name") or item.get("name") or model_name).strip()
                description = str(item.get("description") or "").strip() or None

            if not model_name or model_name in seen_names:
                continue
            seen_names.add(model_name)

            static_model = static_models.get(model_name)
            parsed_models.append(
                {
                    "name": model_name,
                    "display_name": static_model.display_name if static_model else display_name or model_name,
                    "description": static_model.description if static_model and static_model.description else description,
                    "supports_vision": static_model.supports_vision if static_model else False,
                    "supports_thinking": static_model.supports_thinking if static_model else False,
                    "supports_reasoning_effort": static_model.supports_reasoning_effort if static_model else False,
                    "provider_code": provider.code,
                    "provider_display_name": provider.display_name,
                    "provider_icon_key": provider.icon_key,
                }
            )

        return parsed_models

    async def _probe_openai_compatible_provider(
        self,
        provider: ProviderDefinition,
        api_key: str,
        *,
        base_url: str | None = None,
    ) -> None:
        if not provider.models:
            return

        client = get_http_client()
        resolved_base_url = str(base_url or provider.base_url or "").strip()
        if not resolved_base_url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先填写 Base URL")
        probe_url = f"{resolved_base_url.rstrip('/')}/chat/completions"
        last_status_error: HTTPException | None = None

        for candidate_model in provider.models:
            try:
                response = await client.post(
                    probe_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": candidate_model.name,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                )
                response.raise_for_status()
                return
            except httpx.HTTPStatusError as exc:
                detail = "拉取供应商模型列表失败"
                if exc.response.status_code in {401, 403}:
                    detail = "API Key 无效或没有获取模型列表的权限"
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
                last_status_error = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
            except HTTPException:
                raise
            except httpx.HTTPError as exc:
                logger.exception("Failed to probe provider models for '%s'", provider.code)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="连接模型供应商失败，请稍后重试",
                ) from exc

        if last_status_error is not None:
            raise last_status_error

    async def _run_provider_model_health_check(
        self,
        provider: ProviderDefinition,
        model_name: str,
        api_key: str,
        *,
        base_url: str | None = None,
    ) -> None:
        resolved_base_url = str(base_url or provider.base_url or "").strip()
        if not resolved_base_url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先填写 Base URL")

        client = get_http_client()
        try:
            if provider.remote_models_format == "anthropic":
                response = await client.post(
                    f"{resolved_base_url.rstrip('/')}/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
            elif provider.remote_models_format == "gemini":
                response = await client.post(
                    f"{resolved_base_url.rstrip('/')}/v1beta/models/{model_name}:generateContent",
                    params={"key": api_key},
                    json={
                        "contents": [
                            {
                                "role": "user",
                                "parts": [{"text": "hi"}],
                            }
                        ]
                    },
                )
            else:
                response = await client.post(
                    f"{resolved_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = "模型健康检测失败"
            if exc.response.status_code in {401, 403}:
                detail = "API Key 无效或没有访问该模型的权限"
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
        except httpx.HTTPError as exc:
            logger.exception("Failed to run health check for '%s' model '%s'", provider.code, model_name)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="连接模型供应商失败，请稍后重试",
            ) from exc

    async def _build_provider_credentials_map(self, user_id: UUID) -> dict[str, dict[str, Any]]:
        credentials = await self.provider_repo.list_by_user(user_id)
        credentials_by_provider = {
            credential.provider_code: credential
            for credential in credentials
            if credential.is_active
        }
        provider_credentials: dict[str, dict[str, Any]] = {}
        for provider in list_provider_definitions():
            credential = credentials_by_provider.get(provider.code)
            if credential is None:
                continue
            provider_credentials[provider.code] = {
                "credential_configured": True,
                "api_key_masked": credential.api_key_masked,
                "base_url": self._resolve_provider_base_url(provider, credential),
            }
        return provider_credentials

    @staticmethod
    def _serialize_provider_model(provider: ProviderDefinition, model: ProviderModelDefinition) -> dict[str, Any]:
        return {
            "name": model.name,
            "display_name": model.display_name,
            "description": model.description,
            "supports_vision": model.supports_vision,
            "supports_thinking": model.supports_thinking,
            "supports_reasoning_effort": model.supports_reasoning_effort,
            "provider_code": provider.code,
            "provider_display_name": provider.display_name,
            "provider_icon_key": provider.icon_key,
        }

    @staticmethod
    def _resolve_provider_base_url(provider: ProviderDefinition, credential: Any | None = None) -> str:
        if provider.code == "custom":
            return str(getattr(credential, "custom_base_url", "") or "").strip()
        return str(provider.base_url or "").strip()

    @staticmethod
    def _resolve_preview_base_url(
        provider: ProviderDefinition,
        credential: Any | None,
        requested_base_url: str | None,
    ) -> str:
        if provider.code == "custom":
            normalized = str(requested_base_url or "").strip()
            if normalized:
                return ModelConfigService._resolve_requested_base_url(provider, normalized)
            return ModelConfigService._resolve_provider_base_url(provider, credential)
        return str(provider.base_url or "").strip()

    @staticmethod
    def _resolve_requested_base_url(provider: ProviderDefinition, requested_base_url: str | None) -> str:
        if provider.code != "custom":
            return str(provider.base_url or "").strip()

        normalized = str(requested_base_url or "").strip()
        if not normalized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先填写 Base URL")
        if not normalized.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Base URL 必须以 http:// 或 https:// 开头",
            )
        return normalized.rstrip("/")

    @staticmethod
    def _require_provider(provider_code: str) -> ProviderDefinition:
        provider = get_provider_definition(provider_code)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="不支持该模型供应商")
        return provider

    async def _resolve_provider_access(
        self,
        user_id: UUID,
        provider: ProviderDefinition,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> tuple[Any | None, str, str]:
        credential = await self.provider_repo.get_by_user_and_provider(user_id, provider.code)
        normalized_api_key = str(api_key or "").strip()
        if normalized_api_key:
            effective_api_key = normalized_api_key
        else:
            if credential is None or not credential.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="请先填写该供应商的 API Key",
                )
            try:
                effective_api_key = decrypt_api_key(credential.api_key_encrypted)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        resolved_base_url = self._resolve_preview_base_url(provider, credential, base_url)
        return credential, effective_api_key, resolved_base_url

    @staticmethod
    def _find_remote_provider_model(remote_models: list[dict[str, Any]], model_name: str) -> dict[str, Any]:
        normalized_model_name = str(model_name or "").strip()
        for item in remote_models:
            if str(item.get("name") or "").strip() == normalized_model_name:
                return item
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该供应商下不存在此模型")

    @staticmethod
    def _parse_runtime_binding_identity(binding_id: Any, user_id: Any) -> tuple[UUID, UUID]:
        try:
            binding_uuid = UUID(str(binding_id))
            user_uuid = UUID(str(user_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="模型绑定令牌缺少有效标识") from exc
        return binding_uuid, user_uuid

    @staticmethod
    def _raise_integrity_error(exc: IntegrityError) -> None:
        message = str(getattr(exc, "orig", exc)).lower()
        if "uq_user_model_bindings_user_provider_model" in message or "user_model_bindings" in message:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该模型已添加") from exc
        if "uq_user_model_provider_credentials_user_provider" in message or "user_model_provider_credentials" in message:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该供应商配置已存在，请刷新后重试") from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型配置冲突，请刷新后重试") from exc
