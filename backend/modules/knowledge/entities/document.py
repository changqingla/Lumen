"""Document database model."""
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, func, BigInteger, Text
from sqlalchemy.dialects.postgresql import UUID
from config.database import Base
import uuid


class Document(Base):
    """Document model for knowledge base documents."""
    __tablename__ = "kb_documents"
    
    # Document statuses
    STATUS_UPLOADING = "uploading"
    STATUS_PROCESSING = "processing"
    STATUS_CHUNKING = "chunking"
    STATUS_EMBEDDING = "embedding"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kb_id = Column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    size = Column(BigInteger, nullable=False, default=0)
    status = Column(String, nullable=False, default=STATUS_UPLOADING)
    source = Column(String, nullable=False)  # upload/url
    file_path = Column(String, nullable=True)  # MinIO path for original file
    markdown_path = Column(String, nullable=True)  # MinIO path for converted markdown
    mineru_task_id = Column(String, nullable=True)  # Mineru task ID for tracking
    parse_task_id = Column(String, nullable=True)  # Document processing task ID
    chunk_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "kbId": str(self.kb_id),
            "name": self.name,
            "size": self.size,
            "status": self.status,
            "chunkCount": self.chunk_count,
            "uploadedAt": self.created_at.isoformat(),
            "createdAt": self.created_at.isoformat(),  # Keep for compatibility
        }

