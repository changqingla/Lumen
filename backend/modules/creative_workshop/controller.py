"""Creative Workshop API endpoints."""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from config.settings import settings
from middlewares.auth import get_current_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/creative-workshop", tags=["Creative Workshop"])

ImageSize = Literal[
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "2048x2048",
    "3840x2160",
    "2160x3840",
    "auto",
]
ImageQuality = Literal["low", "medium", "high", "auto"]
ImageOutputFormat = Literal["png", "jpeg", "webp"]


class ImageGenerationRequest(BaseModel):
    """Request payload for image generation."""

    prompt: str = Field(..., min_length=1, max_length=4000)
    size: ImageSize = "1024x1024"
    quality: ImageQuality = "medium"
    output_format: ImageOutputFormat = "jpeg"
    output_compression: int | None = Field(default=80, ge=0, le=100)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("请输入图片提示词")
        return normalized


class ImageGenerationResponse(BaseModel):
    """Response payload returned to the web app."""

    b64_json: str
    mime_type: str
    model: str
    size: str
    quality: str
    output_format: str


def _image_api_url() -> str:
    return f"{settings.CREATIVE_WORKSHOP_IMAGE_BASE_URL.rstrip('/')}/images/generations"


@router.post("/images/generations", response_model=ImageGenerationResponse)
async def generate_image(
    request: ImageGenerationRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate an image through the configured image model provider."""
    api_key = (settings.CREATIVE_WORKSHOP_IMAGE_API_KEY or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "IMAGE_API_NOT_CONFIGURED", "message": "创意工坊图片服务尚未配置"}},
        )

    payload: dict[str, object] = {
        "model": settings.CREATIVE_WORKSHOP_IMAGE_MODEL,
        "prompt": request.prompt,
        "size": request.size,
        "quality": request.quality,
        "output_format": request.output_format,
    }
    if request.output_format in {"jpeg", "webp"} and request.output_compression is not None:
        payload["output_compression"] = request.output_compression

    try:
        async with httpx.AsyncClient(timeout=settings.CREATIVE_WORKSHOP_IMAGE_TIMEOUT) as client:
            response = await client.post(
                _image_api_url(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        provider_message = "图片生成失败"
        try:
            error_payload = exc.response.json()
            error = error_payload.get("error") if isinstance(error_payload, dict) else None
            if isinstance(error, dict):
                provider_message = str(error.get("message") or provider_message)
            elif isinstance(error_payload, dict) and error_payload.get("message"):
                provider_message = str(error_payload.get("message"))
        except Exception:
            provider_message = exc.response.text[:300] or provider_message

        logger.warning(
            "Image generation provider rejected request for user=%s status=%s",
            current_user.id,
            exc.response.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "IMAGE_PROVIDER_ERROR", "message": provider_message}},
        ) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"error": {"code": "IMAGE_PROVIDER_TIMEOUT", "message": "图片生成超时，请稍后重试"}},
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Image generation provider request failed for user=%s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "IMAGE_PROVIDER_UNAVAILABLE", "message": "图片服务暂时不可用"}},
        ) from exc

    images = data.get("data") if isinstance(data, dict) else None
    first_image = images[0] if isinstance(images, list) and images else None
    b64_json = first_image.get("b64_json") if isinstance(first_image, dict) else None
    if not isinstance(b64_json, str) or not b64_json.strip():
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "IMAGE_PROVIDER_BAD_RESPONSE", "message": "图片服务返回了无法识别的结果"}},
        )

    return ImageGenerationResponse(
        b64_json=b64_json,
        mime_type=f"image/{request.output_format}",
        model=settings.CREATIVE_WORKSHOP_IMAGE_MODEL,
        size=request.size,
        quality=request.quality,
        output_format=request.output_format,
    )
