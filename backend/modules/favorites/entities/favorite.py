"""Favorite database model."""
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Index, func
from sqlalchemy.dialects.postgresql import UUID
from config.database import Base
import uuid


class Favorite(Base):
    """Favorite model - unified table for KB and document favorites."""
    __tablename__ = "favorites"
    
    # Item types
    ITEM_TYPE_KB = "knowledge_base"
    ITEM_TYPE_DOC = "document"
    
    # Sources
    SOURCE_SUBSCRIPTION = "subscription"
    SOURCE_MANUAL = "manual"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    item_type = Column(String(20), nullable=False)  # 'knowledge_base' or 'document'
    item_id = Column(UUID(as_uuid=True), nullable=False)
    source = Column(String(20), nullable=True)  # 'subscription' or 'manual'
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    __table_args__ = (
        UniqueConstraint('user_id', 'item_type', 'item_id', name='uq_user_item_favorite'),
        Index('idx_favorites_user_type', 'user_id', 'item_type'),
    )
    
    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "userId": str(self.user_id),
            "itemType": self.item_type,
            "itemId": str(self.item_id),
            "source": self.source,
            "createdAt": self.created_at.isoformat(),
        }
