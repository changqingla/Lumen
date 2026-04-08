"""Model provider configuration endpoints."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.settings import settings
from middlewares.auth import get_current_user
from models.user import User

router = APIRouter(prefix="/model-config", tags=["Model Config"])
internal_router = APIRouter(prefix="/internal", tags=["Internal Model Config"])


def _create_model_config_service(db: AsyncSession):
    from modules.model_config.services.model_config_service import ModelConfigService

    return ModelConfigService(db)


def _verify_internal_request(x_internal_token: str = Header(default="", alias="X-Internal-Token")) -> None:
    expected = str(settings.RAG_INTERNAL_API_TOKEN or "").strip()
    provided = str(x_internal_token or "").strip()
    if not expected or provided != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized internal request")


class ProviderModelResponse(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    supports_vision: bool = False
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    provider_code: str
    provider_display_name: str
    provider_icon_key: str


class ProviderResponse(BaseModel):
    code: str
    display_name: str
    description: str
    icon_key: str
    api_key_label: str
    base_url: str
    credential_configured: bool = False
    api_key_masked: str | None = None
    models: list[ProviderModelResponse]


class ModelConfigCatalogResponse(BaseModel):
    providers: list[ProviderResponse]
    user_models: list[dict]


class ModelBindingResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: str | None = None
    provider_code: str
    provider_display_name: str
    provider_icon_key: str
    provider_model_name: str
    supports_vision: bool = False
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    is_enabled: bool = True
    health_status: str = "unknown"
    last_health_checked_at: str | None = None
    last_health_latency_ms: int | None = None
    last_health_error: str | None = None
    source: Literal["user"] = "user"


class ProviderRemoteModelsResponse(BaseModel):
    provider_code: str
    provider_display_name: str
    provider_icon_key: str
    base_url: str
    models: list[ProviderModelResponse]


class PreviewProviderRemoteModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = None
    base_url: str | None = None


class SaveProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = None
    base_url: str | None = None


class SaveProviderCredentialResponse(BaseModel):
    provider_code: str
    provider_display_name: str
    provider_icon_key: str
    api_key_masked: str
    credential_configured: bool


class CreateModelBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_code: str = Field(..., min_length=1)
    provider_model_name: str = Field(..., min_length=1)
    api_key: str | None = None
    base_url: str | None = None


class DeleteModelBindingResponse(BaseModel):
    success: Literal[True] = True


class DeleteProviderCredentialResponse(BaseModel):
    success: Literal[True] = True
    provider_code: str
    provider_display_name: str
    removed_bindings_count: int


class UpdateModelBindingEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool


class UpdateProviderBindingsEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool


class ClearModelHealthStatusesResponse(BaseModel):
    success: Literal[True] = True
    cleared_count: int


class ResolveRuntimeBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)


class ResolvedRuntimeBindingResponse(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    use: str
    supports_vision: bool = False
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    config: dict


@router.post("/health/reset", response_model=ClearModelHealthStatusesResponse)
async def clear_model_health_statuses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.clear_model_health_statuses(current_user.id)
    return ClearModelHealthStatusesResponse(**result)


@router.get("", response_model=ModelConfigCatalogResponse)
async def get_model_config_catalog(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    catalog = await model_config_service.list_model_config_page(current_user.id)
    return ModelConfigCatalogResponse(**catalog)


@router.get("/providers", response_model=list[ProviderResponse])
async def list_model_providers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    providers = await model_config_service.list_provider_catalog_for_user(current_user.id)
    return [ProviderResponse(**provider) for provider in providers]


@router.post("/providers/{provider_code}/credential", response_model=SaveProviderCredentialResponse)
async def save_provider_credential(
    provider_code: str,
    request: SaveProviderCredentialRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.save_provider_credential(
        current_user.id,
        provider_code,
        request.api_key,
        base_url=request.base_url,
    )
    return SaveProviderCredentialResponse(**result)


@router.delete("/providers/{provider_code}/credential", response_model=DeleteProviderCredentialResponse)
async def delete_provider_credential(
    provider_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.delete_provider_credential(current_user.id, provider_code)
    return DeleteProviderCredentialResponse(**result)


@router.get("/models", response_model=list[ModelBindingResponse])
async def list_user_model_bindings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    return await model_config_service.list_user_model_bindings(current_user.id)


@router.get("/providers/{provider_code}/models", response_model=ProviderRemoteModelsResponse)
async def list_provider_remote_models(
    provider_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.list_remote_provider_models(current_user.id, provider_code)
    return ProviderRemoteModelsResponse(**result)


@router.post("/providers/{provider_code}/models/preview", response_model=ProviderRemoteModelsResponse)
async def preview_provider_remote_models(
    provider_code: str,
    request: PreviewProviderRemoteModelsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.preview_remote_provider_models(
        current_user.id,
        provider_code,
        api_key=request.api_key,
        base_url=request.base_url,
    )
    return ProviderRemoteModelsResponse(**result)


@router.post("/models", response_model=ModelBindingResponse)
async def create_user_model_binding(
    request: CreateModelBindingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    return await model_config_service.create_model_binding(
        current_user.id,
        request.provider_code,
        request.provider_model_name,
        api_key=request.api_key,
        base_url=request.base_url,
    )


@router.patch("/models/{binding_id}/enabled", response_model=ModelBindingResponse)
async def update_user_model_binding_enabled(
    binding_id: UUID,
    request: UpdateModelBindingEnabledRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.update_model_binding_enabled(
        current_user.id,
        binding_id,
        is_enabled=request.is_enabled,
    )
    return ModelBindingResponse(**result)


@router.patch("/providers/{provider_code}/enabled", response_model=list[ModelBindingResponse])
async def update_provider_bindings_enabled(
    provider_code: str,
    request: UpdateProviderBindingsEnabledRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.update_provider_bindings_enabled(
        current_user.id,
        provider_code,
        is_enabled=request.is_enabled,
    )
    return [ModelBindingResponse(**item) for item in result]


@router.post("/models/{binding_id}/health-check", response_model=ModelBindingResponse)
async def run_model_binding_health_check(
    binding_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    result = await model_config_service.run_model_binding_health_check(current_user.id, binding_id)
    return ModelBindingResponse(**result)


@router.delete("/models/{binding_id}", response_model=DeleteModelBindingResponse)
async def delete_user_model_binding(
    binding_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    await model_config_service.delete_model_binding(current_user.id, binding_id)
    return DeleteModelBindingResponse()


@internal_router.post("/runtime-model-bindings/resolve", response_model=ResolvedRuntimeBindingResponse)
async def resolve_runtime_model_binding(
    request: ResolveRuntimeBindingRequest,
    _verified: None = Depends(_verify_internal_request),
    db: AsyncSession = Depends(get_db),
):
    model_config_service = _create_model_config_service(db)
    resolved = await model_config_service.resolve_runtime_binding(
        request.token,
        thread_id=request.thread_id,
    )
    return ResolvedRuntimeBindingResponse(**resolved)
