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


def _image_api_url(path: str) -> str:
    return f"{settings.CREATIVE_WORKSHOP_IMAGE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"


def _get_image_api_key() -> str:
    api_key = (settings.CREATIVE_WORKSHOP_IMAGE_API_KEY or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "IMAGE_API_NOT_CONFIGURED", "message": "创意工坊图片服务尚未配置"}},
        )
    return api_key


def _extract_provider_message(response: httpx.Response) -> str:
    provider_message = "图片生成失败"
    response_text = response.text[:300]
    content_type = str(response.headers.get("content-type") or "").lower()
    if "text/html" in content_type or response_text.lstrip().lower().startswith("<!doctype html"):
        if response.status_code == 502:
            return "图片服务网关错误：上游图片接口暂时不可用"
        return f"图片服务返回了 HTML 错误页（HTTP {response.status_code}）"

    try:
        error_payload = response.json()
        error = error_payload.get("error") if isinstance(error_payload, dict) else None
        if isinstance(error, dict):
            provider_message = str(error.get("message") or provider_message)
        elif isinstance(error_payload, dict) and error_payload.get("message"):
            provider_message = str(error_payload.get("message"))
    except Exception:
        provider_message = response_text or provider_message
    return provider_message


async def _post_image_provider_json(
    *,
    path: str,
    payload: dict[str, object],
    user_id: object,
) -> dict:
    api_key = _get_image_api_key()

    try:
        async with httpx.AsyncClient(timeout=settings.CREATIVE_WORKSHOP_IMAGE_TIMEOUT) as client:
            response = await client.post(
                _image_api_url(path),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        provider_message = _extract_provider_message(exc.response)
        logger.warning(
            "Image provider rejected JSON request for user=%s status=%s path=%s",
            user_id,
            exc.response.status_code,
            path,
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
        logger.warning("Image provider JSON request failed for user=%s path=%s: %s", user_id, path, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "IMAGE_PROVIDER_UNAVAILABLE", "message": "图片服务暂时不可用"}},
        ) from exc

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "IMAGE_PROVIDER_BAD_RESPONSE", "message": "图片服务返回了无法识别的结果"}},
        )
    return data


def _build_provider_payload(request: ImageGenerationRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": settings.CREATIVE_WORKSHOP_IMAGE_MODEL,
        "prompt": request.prompt,
        "size": request.size,
        "quality": request.quality,
        "output_format": request.output_format,
    }
    if request.output_format in {"jpeg", "webp"} and request.output_compression is not None:
        payload["output_compression"] = request.output_compression
    return payload


def _extract_image_response(data: dict, request: ImageGenerationRequest) -> ImageGenerationResponse:
    images = data.get("data")
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


@router.post("/images/generations", response_model=ImageGenerationResponse)
async def generate_image(
    request: ImageGenerationRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate an image through the configured image model provider."""
    data = await _post_image_provider_json(
        path="/images/generations",
        payload=_build_provider_payload(request),
        user_id=current_user.id,
    )
    return _extract_image_response(data, request)
