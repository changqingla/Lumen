"""Notes API endpoints."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from config.database import get_db
from modules.notes.schemas import (
    BatchDeleteNotesRequest,
    CreateNoteRequest,
    NoteFolderRequest,
    NoteItem,
    NoteFolderItem,
    UpdateNoteRequest,
)
from middlewares.auth import get_current_user
from models.user import User

router = APIRouter(prefix="/notes", tags=["Notes"])


def _create_note_service(db: AsyncSession):
    from modules.notes.services.note_service import NoteService

    return NoteService(db)


@router.get("/folders")
async def list_folders(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List note folders."""
    service = _create_note_service(db)
    folders = await service.list_folders(str(current_user.id))
    return {"folders": [{"id": f["id"], "name": f["name"], "noteCount": f["count"], "createdAt": ""} for f in folders]}


@router.post("/folders")
async def create_folder(
    request: NoteFolderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new folder."""
    service = _create_note_service(db)
    folder = await service.create_folder(str(current_user.id), request.name)
    return {"id": str(folder["id"]), "name": folder["name"]}


@router.patch("/folders/{folderId}")
async def rename_folder(
    folderId: str,
    request: NoteFolderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Rename a folder."""
    service = _create_note_service(db)
    await service.rename_folder(folderId, str(current_user.id), request.name)
    return {"success": True}


@router.delete("/folders/{folderId}")
async def delete_folder(
    folderId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a folder."""
    service = _create_note_service(db)
    await service.delete_folder(folderId, str(current_user.id))
    return {"success": True}


@router.get("")
async def list_notes(
    folderId: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List notes with pagination."""
    service = _create_note_service(db)
    items, total = await service.list_notes(
        str(current_user.id), folderId, query, page, pageSize
    )
    return {"total": total, "page": page, "pageSize": pageSize, "items": items}


@router.get("/{noteId}", response_model=NoteItem)
async def get_note(
    noteId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get note details."""
    service = _create_note_service(db)
    return await service.get_note(noteId, str(current_user.id))


@router.post("", response_model=dict)
async def create_note(
    request: CreateNoteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new note."""
    service = _create_note_service(db)
    note = await service.create_note(
        str(current_user.id),
        request.title,
        request.content,
        request.folder,
        request.tags
    )
    return {"id": note["id"]}


@router.patch("/{noteId}", response_model=NoteItem)
async def update_note(
    noteId: str,
    request: UpdateNoteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a note."""
    service = _create_note_service(db)
    update_data = request.dict(exclude_unset=True)
    return await service.update_note(noteId, str(current_user.id), **update_data)


@router.delete("/{noteId}")
async def delete_note(
    noteId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a note."""
    service = _create_note_service(db)
    await service.delete_note(noteId, str(current_user.id))
    return {"success": True}


@router.post(":batchDelete")
async def batch_delete_notes(
    request: BatchDeleteNotesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Batch delete notes."""
    service = _create_note_service(db)
    await service.batch_delete_notes(str(current_user.id), request.ids)
    return {"success": True}


@router.post("/{noteId}:polish")
async def polish_note(
    noteId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """AI polish note content."""
    service = _create_note_service(db)
    note = await service.get_note(noteId, str(current_user.id))
    
    # Simple polishing rules (demo)
    content = note["content"]
    content = content.replace("- ", "• ")
    content = "\n".join(line.rstrip() for line in content.split("\n"))
    
    return {"content": content}
