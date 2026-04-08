"""Note and NoteFolder database models."""
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from config.database import Base
import uuid


class NoteFolder(Base):
    """Note folder model."""
    __tablename__ = "note_folders"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    __table_args__ = (
        UniqueConstraint('user_id', 'name', name='uq_user_folder_name'),
    )
    
    notes = relationship("Note", back_populates="folder")


class Note(Base):
    """Note model."""
    __tablename__ = "notes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    folder_id = Column(UUID(as_uuid=True), ForeignKey("note_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False, default="")
    tags = Column(ARRAY(String), nullable=False, default=list, server_default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    folder = relationship("NoteFolder", back_populates="notes")
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "title": self.title,
            "content": self.content,
            "folder": self.folder.name if self.folder else "未分类",
            "tags": self.tags or [],
            "updatedAt": self.updated_at.isoformat(),
            "createdAt": self.created_at.isoformat(),
        }

