"""Chat model catalog endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from middlewares.auth import AuthenticatedIdentity, get_current_chat_identity
router = APIRouter(prefix="/rag", tags=["RAG"])


def _get_insight_runtime_service():
    from modules.chat.services.insight_runtime_service import insight_runtime_service

    return insight_runtime_service


class ChatModelResponse(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    supports_vision: bool = False
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    provider_code: str = "system"
    provider_display_name: str = "系统默认"
    provider_icon_key: str = "system"
    source: str = "system"


class ChatModelsListResponse(BaseModel):
    default_model: str
    models: list[ChatModelResponse]


@router.get("/models", response_model=ChatModelsListResponse)
async def list_chat_models(
    identity: AuthenticatedIdentity = Depends(get_current_chat_identity),
    db: AsyncSession = Depends(get_db),
):
    """List runtime-backed selectable chat models."""
    current_user = identity.user
    insight_runtime_service = _get_insight_runtime_service()
    runtime_models = await insight_runtime_service.list_runtime_models()
    from modules.model_config.services.model_config_service import ModelConfigService

    model_config_service = ModelConfigService(db)
    system_models = [
        ChatModelResponse(**model_config_service.serialize_system_model(item))
        for item in runtime_models
        if str(item.get("name") or "").strip()
    ]
    user_models = []
    if not identity.is_guest:
        user_models = [
            ChatModelResponse(**item)
            for item in await model_config_service.list_user_model_bindings(current_user.id)
        ]
    models = [*system_models, *user_models]
    if not models:
        raise HTTPException(status_code=503, detail="当前没有可用模型")
    default_model = models[0].name
    return ChatModelsListResponse(default_model=default_model, models=models)
