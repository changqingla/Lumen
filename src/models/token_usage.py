"""Token usage record database model."""
from sqlalchemy import Column, String, Integer, DateTime, Index, func
from sqlalchemy.dialects.postgresql import UUID
from config.database import Base
import uuid


class TokenUsageRecord(Base):
    """Token usage record for tracking LLM API consumption."""
    __tablename__ = "token_usage_records"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    session_id = Column(String(255), nullable=True)
    model_name = Column(String(100), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    request_type = Column(String(50), nullable=True)  # chat/summary/comparison 等
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # 复合索引优化查询
    __table_args__ = (
        Index('idx_token_usage_user_created', 'user_id', 'created_at'),
        Index('idx_token_usage_created', 'created_at'),
    )
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "session_id": self.session_id,
            "model_name": self.model_name,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "request_type": self.request_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
