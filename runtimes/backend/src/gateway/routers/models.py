from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import get_app_config

router = APIRouter(prefix="/api", tags=["models"])


class ModelResponse(BaseModel):
    """模型信息响应模型。"""

    name: str = Field(..., description="Unique identifier for the model")
    display_name: str | None = Field(None, description="Human-readable name")
    description: str | None = Field(None, description="Model description")
    supports_vision: bool = Field(default=False, description="Whether model supports image input")
    supports_thinking: bool = Field(default=False, description="Whether model supports thinking mode")
    supports_reasoning_effort: bool = Field(default=False, description="Whether model supports reasoning effort")


class ModelsListResponse(BaseModel):
    """模型列表响应模型。"""

    models: list[ModelResponse]


@router.get(
    "/models",
    response_model=ModelsListResponse,
    summary="List All Models",
    description="Retrieve a list of all available AI models configured in the system.",
)
async def list_models() -> ModelsListResponse:
    """获取系统中全部可用模型列表。

    返回面向前端展示的模型信息，不包含 API Key 等敏感字段
    以及内部实现配置。

    返回：
        所有已配置模型及其元数据列表。
    """
    config = get_app_config()
    models = [
        ModelResponse(
            name=model.name,
            display_name=model.display_name,
            description=model.description,
            supports_vision=model.supports_vision,
            supports_thinking=model.supports_thinking,
            supports_reasoning_effort=model.supports_reasoning_effort,
        )
        for model in config.models
    ]
    return ModelsListResponse(models=models)


@router.get(
    "/models/{model_name}",
    response_model=ModelResponse,
    summary="Get Model Details",
    description="Retrieve detailed information about a specific AI model by its name.",
)
async def get_model(model_name: str) -> ModelResponse:
    """获取指定模型详情。

    参数：
        model_name: 要查询的模型唯一名称。

    返回：
        找到时返回模型信息。

    异常：
        HTTPException: 模型不存在时返回 404。
    """
    config = get_app_config()
    model = config.get_model_config(model_name)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    return ModelResponse(
        name=model.name,
        display_name=model.display_name,
        description=model.description,
        supports_vision=model.supports_vision,
        supports_thinking=model.supports_thinking,
        supports_reasoning_effort=model.supports_reasoning_effort,
    )
