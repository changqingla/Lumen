"""Note repository for database operations."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from typing import Optional, List, Tuple
from modules.notes.entities.note import Note, NoteFolder


class NoteRepository:
    """Repository for Note model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_by_id(self, note_id: str, user_id: str) -> Optional[Note]:
        """Get note by ID for specific user."""
        result = await self.db.execute(
            select(Note)
            .options(selectinload(Note.folder))
            .where(Note.id == note_id, Note.user_id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def list_notes(
        self,
        user_id: str,
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[Note], int]:
        """List notes with pagination."""
        stmt = select(Note).options(selectinload(Note.folder)).where(Note.user_id == user_id)
        
        if folder_id:
            stmt = stmt.where(Note.folder_id == folder_id)
        
        if query:
            stmt = stmt.where(Note.title.ilike(f"%{query}%"))
        
        # Count total
        count_stmt = select(func.count()).select_from(
            select(Note).where(Note.user_id == user_id).subquery()
        )
        if folder_id:
            count_stmt = select(func.count()).select_from(
                select(Note).where(Note.user_id == user_id, Note.folder_id == folder_id).subquery()
            )
        if query:
            count_stmt = select(func.count()).select_from(
                select(Note).where(Note.user_id == user_id, Note.title.ilike(f"%{query}%")).subquery()
            )
        
        total = (await self.db.execute(count_stmt)).scalar()
        
        # Paginate
        stmt = stmt.order_by(Note.updated_at.desc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        
        result = await self.db.execute(stmt)
        notes = result.scalars().all()
        
        return list(notes), total or 0
    
    async def create(
        self,
        user_id: str,
        title: str,
        content: str,
        folder_id: Optional[str],
        tags: List[str]
    ) -> Note:
        """Create a new note."""
        note = Note(
            user_id=user_id,
            folder_id=folder_id,
            title=title,
            content=content,
            tags=tags
        )
        self.db.add(note)
        await self.db.commit()
        await self.db.refresh(note, ["folder"])
        return note
    
    async def update(self, note: Note, **kwargs) -> Note:
        """Update note fields."""
        for key, value in kwargs.items():
            # folder_id=None is an explicit move to the unfiled collection.
            if hasattr(note, key) and (value is not None or key == "folder_id"):
                setattr(note, key, value)
        
        await self.db.commit()
        await self.db.refresh(note)
        
        # 重新查询以正确加载关系
        result = await self.db.execute(
            select(Note)
            .options(selectinload(Note.folder))
            .where(Note.id == note.id)
        )
        return result.scalar_one()
    
    async def delete(self, note: Note):
        """Delete a note."""
        await self.db.delete(note)
        await self.db.commit()
    
    async def batch_delete(self, user_id: str, note_ids: List[str]):
        """Batch delete notes."""
        await self.db.execute(
            delete(Note).where(Note.user_id == user_id, Note.id.in_(note_ids))
        )
        await self.db.commit()


class NoteFolderRepository:
    """Repository for NoteFolder model."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def list_folders(self, user_id: str) -> List[Tuple[NoteFolder, int]]:
        """List folders with note count."""
        stmt = select(
            NoteFolder,
            func.count(Note.id).label('count')
        ).outerjoin(Note).where(NoteFolder.user_id == user_id).group_by(NoteFolder.id)
        
        result = await self.db.execute(stmt)
        return [(folder, count) for folder, count in result.all()]
    
    async def get_by_id(self, folder_id: str, user_id: str) -> Optional[NoteFolder]:
        """Get folder by ID."""
        result = await self.db.execute(
            select(NoteFolder).where(NoteFolder.id == folder_id, NoteFolder.user_id == user_id)
        )
        return result.scalar_one_or_none()
    
    async def create(self, user_id: str, name: str) -> NoteFolder:
        """Create a new folder."""
        folder = NoteFolder(user_id=user_id, name=name)
        self.db.add(folder)
        await self.db.commit()
        await self.db.refresh(folder)
        return folder
    
    async def update(self, folder: NoteFolder, name: str) -> NoteFolder:
        """Update folder name."""
        folder.name = name
        await self.db.commit()
        await self.db.refresh(folder)
        return folder
    
    async def delete(self, folder: NoteFolder):
        """Delete a folder."""
        await self.db.delete(folder)
        await self.db.commit()
