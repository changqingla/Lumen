"""Creative Workshop API endpoints."""

from __future__ import annotations

import contextlib
import logging
import mimetypes
from datetime import timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
import httpx
from jose import JWTError, jwt
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.redis import get_redis_client
from config.settings import settings
from middlewares.auth import get_current_user, get_current_user_optional
from models.user import User
from utils.audit_logger import record_user_prompt_event
from .paper_translation_service import PaperTranslationQueueItem, PaperTranslationStatus, paper_translation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/creative-workshop", tags=["Creative Workshop"])
PAPER_TRANSLATION_ASSET_TOKEN_PURPOSE = "paper_translation_asset"
PAPER_TRANSLATION_ASSET_TOKEN_EXPIRE_MINUTES = 60

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


class PaperTranslationTaskResponse(BaseModel):
    """Paper translation task state returned to the web app."""

    task_id: str
    status: PaperTranslationStatus
    filename: str
    thread_id: str
    model_name: str | None = None
    created_at: str
    updated_at: str
    error: str | None = None


class PaperTranslationFavoriteResponse(BaseModel):
    """Response returned after favoriting a translated paper."""

    success: bool
    kb_id: str
    document_id: str
    document_name: str


class PaperTranslationFavoriteStatusResponse(BaseModel):
    """Response returned when checking translated paper favorite status."""

    favorited: bool
    kb_id: str | None = None
    document_id: str | None = None
    document_name: str | None = None


def _get_paper_translation_service():
    return paper_translation_service


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
    await record_user_prompt_event(
        event_type="image2_prompt",
        user=current_user,
        prompt=request.prompt,
        metadata={
            "model": settings.CREATIVE_WORKSHOP_IMAGE_MODEL,
            "size": request.size,
            "quality": request.quality,
            "output_format": request.output_format,
            "output_compression": request.output_compression,
        },
    )
    data = await _post_image_provider_json(
        path="/images/generations",
        payload=_build_provider_payload(request),
        user_id=current_user.id,
    )
    return _extract_image_response(data, request)


async def _save_pdf_upload(file: UploadFile, destination: Path) -> int:
    filename = str(file.filename or "").strip()
    content_type = str(file.content_type or "").lower()
    is_pdf = filename.lower().endswith(".pdf") or content_type == "application/pdf"
    if not is_pdf:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "UNSUPPORTED_FORMAT", "message": "仅支持上传 PDF 文件"}},
        )

    total_size = 0
    header = b""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > settings.MAX_UPLOAD_SIZE:
                target.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": "FILE_TOO_LARGE",
                            "message": f"文件过大，最大支持 {settings.MAX_UPLOAD_SIZE // 1024 // 1024}MB",
                        }
                    },
                )
            if len(header) < 1024:
                header += chunk[: 1024 - len(header)]
            target.write(chunk)

    if total_size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "EMPTY_FILE", "message": "PDF 文件为空"}},
        )
    if not header.lstrip().startswith(b"%PDF"):
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_PDF", "message": "无法识别 PDF 文件"}},
        )
    return total_size


def _download_headers(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    }


def _create_paper_translation_asset_token(*, owner_id: str, task_id: str, asset_path: str) -> str:
    from utils.security import create_access_token

    return create_access_token(
        data={
            "purpose": PAPER_TRANSLATION_ASSET_TOKEN_PURPOSE,
            "sub": owner_id,
            "task_id": task_id,
            "asset_path": asset_path,
        },
        expires_delta=timedelta(minutes=PAPER_TRANSLATION_ASSET_TOKEN_EXPIRE_MINUTES),
    )


def _verify_paper_translation_asset_token(*, token: str, task_id: str, asset_path: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
    if payload.get("purpose") != PAPER_TRANSLATION_ASSET_TOKEN_PURPOSE:
        return None
    if payload.get("task_id") != task_id or payload.get("asset_path") != asset_path:
        return None
    owner_id = payload.get("sub")
    if not owner_id:
        return None
    return str(owner_id)


def _paper_translation_temp_pdf_path(service: object) -> Path:
    storage_root = Path(getattr(service, "storage_root", settings.CREATIVE_WORKSHOP_PAPER_TRANSLATION_STORAGE_DIR))
    temp_dir = storage_root / "_incoming"
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception("Paper translation storage is not writable: %s", temp_dir)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "STORAGE_UNAVAILABLE", "message": "论文翻译存储目录不可写，请检查服务配置"}},
        ) from exc
    return temp_dir / f"{uuid4().hex}.pdf"


@router.post("/paper-translation/tasks", response_model=PaperTranslationTaskResponse)
async def create_paper_translation_task(
    file: UploadFile = File(...),
    model_name: str | None = Form(default=None, max_length=255),
    current_user: User = Depends(get_current_user),
):
    """Create a paper translation task and run it in the background."""
    service = _get_paper_translation_service()
    owner_id = str(current_user.id)
    filename = file.filename or "paper.pdf"
    normalized_model_name = model_name.strip() if isinstance(model_name, str) else ""
    normalized_model_name = normalized_model_name or None
    temp_pdf_path = _paper_translation_temp_pdf_path(service)
    task = None

    try:
        size_bytes = await _save_pdf_upload(file, temp_pdf_path)
        task = await service.create_task(
            owner_id=owner_id,
            filename=filename,
            model_name=normalized_model_name,
        )
        source_pdf_path = service.source_pdf_path(owner_id=owner_id, task_id=task.task_id)
        source_pdf_path.parent.mkdir(parents=True, exist_ok=True)
        temp_pdf_path.replace(source_pdf_path)
        await service.attach_source_pdf(owner_id=owner_id, task_id=task.task_id, source_pdf_path=source_pdf_path)
    except HTTPException:
        temp_pdf_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_pdf_path.unlink(missing_ok=True)
        if task is not None:
            with contextlib.suppress(Exception):
                await service.mark_task_failed(owner_id=owner_id, task_id=task.task_id, error="上传文件保存失败，请重新上传")
        logger.exception("Failed to create paper translation task for user=%s", owner_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": "UPLOAD_SAVE_FAILED", "message": "上传文件保存失败，请重新上传"}},
        ) from exc

    await record_user_prompt_event(
        event_type="paper_translation_upload",
        user=current_user,
        prompt=filename,
        metadata={
            "task_id": task.task_id,
            "filename": filename,
            "size_bytes": size_bytes,
            "model_name": normalized_model_name,
        },
    )

    try:
        redis_client = await get_redis_client()
        await service.enqueue_translation_task(
            redis_client,
            PaperTranslationQueueItem(
                owner_id=owner_id,
                task_id=task.task_id,
                filename=filename,
                source_pdf_path=str(source_pdf_path),
                model_name=normalized_model_name,
            ),
        )
    except Exception as exc:
        logger.exception("Failed to enqueue paper translation task: task_id=%s owner=%s", task.task_id, owner_id)
        await service.mark_task_failed(owner_id=owner_id, task_id=task.task_id, error="翻译任务入队失败，请稍后重试")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "QUEUE_UNAVAILABLE", "message": "翻译任务暂时无法启动，请稍后重试"}},
        ) from exc

    task = await service.get_task(owner_id=owner_id, task_id=task.task_id) or task
    return PaperTranslationTaskResponse(**service.build_response_payload(task))


@router.get("/paper-translation/tasks/{task_id}", response_model=PaperTranslationTaskResponse)
async def get_paper_translation_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get paper translation task status."""
    service = _get_paper_translation_service()
    task = await service.get_task(owner_id=str(current_user.id), task_id=task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "翻译任务不存在"}},
        )
    return PaperTranslationTaskResponse(**service.build_response_payload(task))


@router.get("/paper-translation/tasks/{task_id}/result")
async def get_paper_translation_result(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Return translated Markdown text for rendering."""
    service = _get_paper_translation_service()
    owner_id = str(current_user.id)

    def sign_asset_url(asset_url: str, asset_path: str) -> str:
        separator = "&" if "?" in asset_url else "?"
        token = _create_paper_translation_asset_token(owner_id=owner_id, task_id=task_id, asset_path=asset_path)
        return f"{asset_url}{separator}asset_token={quote(token, safe='')}"

    try:
        _, content = await service.get_translated_markdown(
            owner_id=owner_id,
            task_id=task_id,
            asset_url_prefix=f"/api/creative-workshop/paper-translation/tasks/{quote(task_id, safe='')}/assets",
            sign_asset_url=sign_asset_url,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Markdown 译文尚不可用"}},
        ) from exc

    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/paper-translation/tasks/{task_id}/assets/{asset_path:path}")
async def get_paper_translation_asset(
    task_id: str,
    asset_path: str,
    asset_token: str | None = None,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Return a translated paper asset for Markdown preview."""
    service = _get_paper_translation_service()
    owner_id = str(current_user.id) if current_user is not None else None
    if asset_token:
        owner_id = _verify_paper_translation_asset_token(
            token=asset_token,
            task_id=task_id,
            asset_path=asset_path,
        )
    if not owner_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "图片资源访问凭证无效"}},
        )

    task = await service.get_task(owner_id=owner_id, task_id=task_id)
    if task is None or task.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "翻译任务不存在或尚未完成"}},
        )
    asset_file = service.resolve_asset_file(owner_id=owner_id, task_id=task_id, asset_path=asset_path)
    if asset_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "图片资源不存在"}},
        )

    return Response(
        content=asset_file.read_bytes(),
        media_type=mimetypes.guess_type(asset_file.name)[0] or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/paper-translation/tasks/{task_id}/source")
async def get_paper_translation_source_pdf(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Return the uploaded source PDF for preview restore."""
    service = _get_paper_translation_service()
    try:
        filename, content = await service.get_source_pdf(owner_id=str(current_user.id), task_id=task_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "PDF 原文尚不可用"}},
        ) from exc

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


@router.get("/paper-translation/tasks/{task_id}/markdown")
async def download_paper_translation_markdown(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Download translated Markdown."""
    service = _get_paper_translation_service()
    try:
        filename, content = await service.get_translated_markdown(
            owner_id=str(current_user.id),
            task_id=task_id,
            inline_assets=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Markdown 译文尚不可用"}},
        ) from exc

    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers=_download_headers(filename),
    )


@router.get("/paper-translation/tasks/{task_id}/markdown/knowledge-base")
async def download_paper_translation_markdown_for_knowledge_base(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Download translated Markdown optimized for knowledge base ingestion."""
    service = _get_paper_translation_service()
    try:
        filename, content = await service.get_translated_markdown(
            owner_id=str(current_user.id),
            task_id=task_id,
            inline_assets=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Markdown 译文尚不可用"}},
        ) from exc

    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers=_download_headers(filename),
    )


@router.get("/paper-translation/tasks/{task_id}/pdf")
async def download_paper_translation_pdf(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """Export and download translated PDF."""
    service = _get_paper_translation_service()
    try:
        filename, content = await service.get_translated_pdf(owner_id=str(current_user.id), task_id=task_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "PDF 译文尚不可用"}},
        ) from exc

    return Response(
        content=content,
        media_type="application/pdf",
        headers=_download_headers(filename),
    )


@router.post("/paper-translation/tasks/{task_id}/favorite", response_model=PaperTranslationFavoriteResponse)
async def favorite_paper_translation_result(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add translated Markdown to the default KB and favorite it."""
    service = _get_paper_translation_service()
    owner_id = str(current_user.id)
    try:
        filename, content = await service.get_translated_markdown(
            owner_id=owner_id,
            task_id=task_id,
            inline_assets=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Markdown 译文尚不可用"}},
        ) from exc

    from modules.favorites.services.favorite_service import FavoriteService
    from modules.knowledge.repositories.kb_repository import KnowledgeBaseRepository
    from modules.knowledge.services.document_service import DocumentService

    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_owner_and_name(owner_id, settings.DEFAULT_KB_NAME)
    if kb is None:
        kb = await kb_repo.create(owner_id, settings.DEFAULT_KB_NAME, "", settings.DEFAULT_KB_CATEGORY)

    document_service = DocumentService(db)
    document = await document_service.create_markdown_document_from_content(
        kb_id=str(kb.id),
        user_id=owner_id,
        filename=filename,
        markdown=content,
        source=f"creative_workshop_paper_translation:{task_id}",
    )

    favorite_service = FavoriteService(db)
    await favorite_service.favorite_document(str(document.id), str(kb.id), owner_id)

    return PaperTranslationFavoriteResponse(
        success=True,
        kb_id=str(kb.id),
        document_id=str(document.id),
        document_name=document.name,
    )


@router.get("/paper-translation/tasks/{task_id}/favorite", response_model=PaperTranslationFavoriteStatusResponse)
async def get_paper_translation_favorite_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check whether the translated Markdown document is already favorited."""
    owner_id = str(current_user.id)

    from modules.favorites.services.favorite_service import FavoriteService
    from modules.knowledge.repositories.document_repository import DocumentRepository
    from modules.knowledge.repositories.kb_repository import KnowledgeBaseRepository

    kb_repo = KnowledgeBaseRepository(db)
    kb = await kb_repo.get_by_owner_and_name(owner_id, settings.DEFAULT_KB_NAME)
    if kb is None:
        return PaperTranslationFavoriteStatusResponse(favorited=False)

    doc_repo = DocumentRepository(db)
    document = await doc_repo.get_by_kb_and_source(
        str(kb.id),
        f"creative_workshop_paper_translation:{task_id}",
    )
    if document is None:
        return PaperTranslationFavoriteStatusResponse(favorited=False, kb_id=str(kb.id))

    favorite_service = FavoriteService(db)
    favorite_status = await favorite_service.check_favorites(
        owner_id,
        [{"type": "document", "id": str(document.id)}],
    )

    return PaperTranslationFavoriteStatusResponse(
        favorited=bool(favorite_status.get(f"document:{document.id}")),
        kb_id=str(kb.id),
        document_id=str(document.id),
        document_name=document.name,
    )
