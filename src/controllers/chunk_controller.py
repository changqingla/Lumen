"""Chunk management API endpoints."""
import logging
from fastapi import APIRouter, HTTPException, status

from schemas.chunk_schemas import (
    ChunkListRequest,
    ChunkSearchRequest,
    ChunkEditRequest,
    ChunkBatchEditRequest,
)
from services.chunk_service import ChunkService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chunks", tags=["Chunk Management"])


@router.post("/list")
async def list_chunks(request: ChunkListRequest):
    """List chunks for a given index/document with pagination."""
    try:
        result = await ChunkService.list_chunks(request)
        return {"success": True, "data": result}
    except Exception as e:
        logger.exception("Failed to list chunks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/search")
async def search_chunks(request: ChunkSearchRequest):
    """Search chunks by keyword."""
    try:
        result = await ChunkService.search_chunks(request)
        return {"success": True, "data": result}
    except Exception as e:
        logger.exception("Failed to search chunks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/edit")
async def edit_chunk(request: ChunkEditRequest):
    """Edit a single chunk."""
    try:
        result = await ChunkService.edit_chunk(request)
        return {"success": True, "data": result}
    except Exception as e:
        logger.exception("Failed to edit chunk")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/batch-edit")
async def batch_edit_chunks(request: ChunkBatchEditRequest):
    """Batch edit multiple chunks."""
    try:
        result = await ChunkService.batch_edit_chunks(request)
        return {"success": True, "data": result}
    except Exception as e:
        logger.exception("Failed to batch edit chunks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
