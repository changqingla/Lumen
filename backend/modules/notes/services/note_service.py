"""Note service business logic."""
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from modules.notes.repositories.note_repository import NoteRepository, NoteFolderRepository
from typing import List, Tuple, Optional


class NoteService:
    """Service for note operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.note_repo = NoteRepository(db)
        self.folder_repo = NoteFolderRepository(db)

    async def _validate_folder_access(
        self,
        folder_id: Optional[str],
        user_id: str,
    ) -> Optional[str]:
        """Return a normalized folder ID after verifying user ownership."""
        if not folder_id:
            return None

        folder = await self.folder_repo.get_by_id(folder_id, user_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Folder not found"}},
            )
        return str(folder.id)
    
    async def list_notes(
        self,
        user_id: str,
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[dict], int]:
        """List notes for user."""
        notes, total = await self.note_repo.list_notes(
            user_id, folder_id, query, page, page_size
        )
        return [note.to_dict() for note in notes], total
    
    async def get_note(self, note_id: str, user_id: str) -> dict:
        """Get note details."""
        note = await self.note_repo.get_by_id(note_id, user_id)
        if not note:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Note not found"}}
            )
        return note.to_dict()
    
    async def create_note(
        self,
        user_id: str,
        title: str,
        content: Optional[str],
        folder: Optional[str],
        tags: List[str]
    ) -> dict:
        """Create a new note."""
        folder_id = await self._validate_folder_access(folder, user_id)
        note_content = content if content is not None else ""
        
        note = await self.note_repo.create(
            user_id, title, note_content, folder_id, tags
        )
        return note.to_dict()
    
    async def update_note(
        self,
        note_id: str,
        user_id: str,
        **kwargs
    ) -> dict:
        """Update note."""
        note = await self.note_repo.get_by_id(note_id, user_id)
        if not note:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Note not found"}}
            )
        
        # 字段映射：前端 folderId/folder -> 数据库 folder_id
        if 'folder' in kwargs:
            kwargs['folder_id'] = kwargs.pop('folder')
        if 'folderId' in kwargs:
            kwargs['folder_id'] = kwargs.pop('folderId')

        if 'folder_id' in kwargs:
            kwargs['folder_id'] = await self._validate_folder_access(
                kwargs['folder_id'],
                user_id,
            )
        
        updated = await self.note_repo.update(note, **kwargs)
        return updated.to_dict()
    
    async def delete_note(self, note_id: str, user_id: str):
        """Delete a note."""
        note = await self.note_repo.get_by_id(note_id, user_id)
        if not note:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Note not found"}}
            )
        await self.note_repo.delete(note)
    
    async def batch_delete_notes(self, user_id: str, note_ids: List[str]):
        """Batch delete notes."""
        await self.note_repo.batch_delete(user_id, note_ids)
    
    async def list_folders(self, user_id: str) -> List[dict]:
        """List note folders."""
        folders = await self.folder_repo.list_folders(user_id)
        return [
            {"id": str(folder.id), "name": folder.name, "count": count}
            for folder, count in folders
        ]
    
    async def create_folder(self, user_id: str, name: str) -> dict:
        """Create a new folder."""
        folder = await self.folder_repo.create(user_id, name)
        return {"id": str(folder.id), "name": folder.name}
    
    async def rename_folder(self, folder_id: str, user_id: str, name: str):
        """Rename a folder."""
        folder = await self.folder_repo.get_by_id(folder_id, user_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Folder not found"}}
            )
        
        # 彩蛋：生活文件夹不允许重命名
        if folder.name == '生活':
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "生活才是生命的真谛，不允许重命名"}}
            )
        
        await self.folder_repo.update(folder, name)
    
    async def delete_folder(self, folder_id: str, user_id: str):
        """Delete a folder."""
        folder = await self.folder_repo.get_by_id(folder_id, user_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": "Folder not found"}}
            )
        
        # 彩蛋：生活文件夹不允许删除
        if folder.name == '生活':
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": "生活才是生命的真谛，不允许删除"}}
            )
        
        await self.folder_repo.delete(folder)
