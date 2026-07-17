"""Chunk management API endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException, status

from middlewares.auth import get_current_user
from models.user import User
from schemas.chunk_schemas import (
    ChunkListRequest,
    ChunkSearchRequest,
    ChunkEditRequest,
    ChunkBatchEditRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chunks", tags=["Chunk Management"])
ChunkService = None


def _chunk_internal_error(operation: str, error: BaseException) -> HTTPException:
    logger.error(
        "chunk_api operation=%s error_type=%s",
        operation,
        type(error).__name__,
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Chunk operation failed",
    )


def _get_chunk_service():
    global ChunkService
    if ChunkService is None:
        from modules.knowledge.services.chunk_service import ChunkService as chunk_service_class

        ChunkService = chunk_service_class

    return ChunkService


@router.post("/list")
async def list_chunks(
    request: ChunkListRequest,
    current_user: User = Depends(get_current_user),
):
    """List chunks for a given index/document with pagination."""
    try:
        chunk_service = _get_chunk_service()
        result = await chunk_service.list_chunks(request, str(current_user.id))
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise _chunk_internal_error("list", exc) from exc


@router.post("/search")
async def search_chunks(
    request: ChunkSearchRequest,
    current_user: User = Depends(get_current_user),
):
    """Search chunks by keyword."""
    try:
        chunk_service = _get_chunk_service()
        result = await chunk_service.search_chunks(request, str(current_user.id))
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise _chunk_internal_error("search", exc) from exc


@router.post("/edit")
async def edit_chunk(
    request: ChunkEditRequest,
    current_user: User = Depends(get_current_user),
):
    """Edit a single chunk."""
    try:
        chunk_service = _get_chunk_service()
        result = await chunk_service.edit_chunk(request, str(current_user.id))
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise _chunk_internal_error("edit", exc) from exc


@router.post("/batch-edit")
async def batch_edit_chunks(
    request: ChunkBatchEditRequest,
    current_user: User = Depends(get_current_user),
):
    """Batch edit multiple chunks."""
    try:
        chunk_service = _get_chunk_service()
        result = await chunk_service.batch_edit_chunks(request, str(current_user.id))
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise _chunk_internal_error("batch_edit", exc) from exc
