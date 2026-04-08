"""User-scoped model provider credentials and model bindings."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from config.database import Base


class UserModelProviderCredential(Base):
    """Encrypted provider credential stored per user and provider."""

    __tablename__ = "user_model_provider_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_code", name="uq_user_model_provider_credentials_user_provider"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_code = Column(String(64), nullable=False, index=True)
    custom_base_url = Column(Text, nullable=True)
    api_key_encrypted = Column(Text, nullable=False)
    api_key_masked = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    bindings = relationship(
        "UserModelBinding",
        back_populates="provider_credential",
        cascade="all, delete-orphan",
        lazy="select",
    )


class UserModelBinding(Base):
    """A user-visible selectable model binding under a configured provider."""

    __tablename__ = "user_model_bindings"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider_credential_id",
            "provider_model_name",
            name="uq_user_model_bindings_user_provider_model",
        ),
        UniqueConstraint("binding_name", name="uq_user_model_bindings_binding_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("user_model_provider_credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_code = Column(String(64), nullable=False, index=True)
    binding_name = Column(String(120), nullable=False, index=True)
    provider_model_name = Column(String(200), nullable=False)
    display_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    supports_vision = Column(Boolean, nullable=False, default=False)
    supports_thinking = Column(Boolean, nullable=False, default=False)
    supports_reasoning_effort = Column(Boolean, nullable=False, default=False)
    is_enabled = Column(Boolean, nullable=False, default=True)
    health_status = Column(String(32), nullable=False, default="unknown")
    last_health_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_health_latency_ms = Column(Integer, nullable=True)
    last_health_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    provider_credential = relationship(
        "UserModelProviderCredential",
        back_populates="bindings",
        lazy="select",
    )
