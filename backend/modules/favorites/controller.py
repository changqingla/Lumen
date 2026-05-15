"""Favorite API endpoints."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from config.database import get_db
from middlewares.auth import get_current_user
from models.user import User
from schemas.schemas import FavoriteCheckRequest

router = APIRouter(prefix="/favorites", tags=["Favorites"])


def _create_favorite_service(db: AsyncSession):
    from modules.favorites.services.favorite_service import FavoriteService

    return FavoriteService(db)


# ============ Knowledge Base Favorites ============

@router.post("/kb/{kbId}")
async def favorite_knowledge_base(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Favorite a knowledge base."""
    service = _create_favorite_service(db)
    return await service.favorite_kb(kbId, str(current_user.id))


@router.delete("/kb/{kbId}")
async def unfavorite_knowledge_base(
    kbId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Unfavorite a knowledge base."""
    service = _create_favorite_service(db)
    return await service.unfavorite_kb(kbId, str(current_user.id))


@router.get("/kb")
async def list_favorite_knowledge_bases(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List favorite knowledge bases."""
    service = _create_favorite_service(db)
    items, total = await service.list_favorite_kbs(str(current_user.id), page, pageSize)
    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": items
    }


# ============ Document Favorites ============

@router.post("/document/{docId}")
async def favorite_document(
    docId: str,
    kbId: str = Query(..., description="Knowledge base ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Favorite a document."""
    service = _create_favorite_service(db)
    return await service.favorite_document(docId, kbId, str(current_user.id))


@router.delete("/document/{docId}")
async def unfavorite_document(
    docId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Unfavorite a document."""
    service = _create_favorite_service(db)
    return await service.unfavorite_document(docId, str(current_user.id))


@router.get("/document")
async def list_favorite_documents(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List favorite documents."""
    service = _create_favorite_service(db)
    items, total = await service.list_favorite_docs(str(current_user.id), page, pageSize)
    return {
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "items": items
    }


# ============ Batch Check ============

@router.post("/check")
async def check_favorites(
    request: FavoriteCheckRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Batch check if items are favorited."""
    service = _create_favorite_service(db)
    return await service.check_favorites(str(current_user.id), request.items)
