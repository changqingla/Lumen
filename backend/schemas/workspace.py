"""Workspace attachment schemas shared by Lumen services."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


AttachmentRole = Literal["source", "derived", "artifact"]
AttachmentSourceKind = Literal["user_upload", "kb_export", "system_derived", "agent_generated"]
AttachmentInputMode = Literal["vision_only", "workspace_file", "both"]
AttachmentParseStatus = Literal["none", "pending", "ready", "partial", "failed"]


def _normalize_compact_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _validate_relative_path(value: str, field_name: str) -> str:
    normalized = (value or "").strip().lstrip("/")
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if ".." in normalized or "\\" in normalized:
        raise ValueError(f"{field_name} 包含非法路径字符")
    return normalized


def _normalize_text_list(value: Optional[Iterable[Any]]) -> list[str]:
    if value is None:
        return []
    seen: set[str] = set()
    normalized_items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = _normalize_compact_text(item)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        normalized_items.append(normalized)
    return normalized_items


class WorkspaceAttachmentInput(BaseModel):
    """Attachment payload accepted by Lumen chat APIs."""

    model_config = ConfigDict(extra="forbid")

    attachment_id: Optional[str] = None
    name: str = Field(..., min_length=1)
    object_path: Optional[str] = None
    workspace_path: Optional[str] = None
    mime_type: Optional[str] = None
    source_kind: AttachmentSourceKind = "user_upload"
    role: AttachmentRole = "source"
    input_mode: AttachmentInputMode = "workspace_file"
    size_bytes: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = None
    parent_attachment_id: Optional[str] = None
    view_type: Optional[str] = None
    available_views: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    parse_status: Optional[AttachmentParseStatus] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("attachment_id", "parent_attachment_id")
    @classmethod
    def validate_attachment_id(cls, value: Optional[str]) -> Optional[str]:
        normalized = _normalize_compact_text(value)
        if normalized is None:
            return None
        if "/" in normalized or "\\" in normalized or ".." in normalized:
            raise ValueError("attachment_id 包含非法路径字符")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("name 不能为空")
        if "/" in normalized or "\\" in normalized:
            raise ValueError("name 不能包含路径分隔符")
        return normalized

    @field_validator("object_path")
    @classmethod
    def validate_object_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _validate_relative_path(value, "object_path")

    @field_validator("workspace_path")
    @classmethod
    def validate_workspace_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _validate_relative_path(value, "workspace_path")

    @field_validator("mime_type", "sha256", "view_type")
    @classmethod
    def validate_optional_text(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_compact_text(value)

    @field_validator("available_views", "capabilities", mode="before")
    @classmethod
    def validate_text_lists(cls, value: Optional[Iterable[Any]]) -> list[str]:
        return _normalize_text_list(value)


class WorkspaceAttachmentRecord(BaseModel):
    """Normalized workspace asset persisted in session manifest/message metadata."""

    model_config = ConfigDict(extra="forbid")

    attachment_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    object_path: str = Field(..., min_length=1)
    workspace_path: str = Field(..., min_length=1)
    mime_type: Optional[str] = None
    source_kind: AttachmentSourceKind = "user_upload"
    role: AttachmentRole = "source"
    input_mode: AttachmentInputMode = "workspace_file"
    size_bytes: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = None
    parent_attachment_id: Optional[str] = None
    view_type: Optional[str] = None
    available_views: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    parse_status: Optional[AttachmentParseStatus] = None
    created_at: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "attachment_id",
        "session_id",
        "user_id",
        "name",
        "created_at",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("object_path", "workspace_path")
    @classmethod
    def validate_required_paths(cls, value: str, info) -> str:
        return _validate_relative_path(value, info.field_name)

    @field_validator("mime_type", "sha256", "parent_attachment_id", "view_type")
    @classmethod
    def validate_optional_metadata(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_compact_text(value)

    @field_validator("available_views", "capabilities", mode="before")
    @classmethod
    def validate_record_lists(cls, value: Optional[Iterable[Any]]) -> list[str]:
        return _normalize_text_list(value)

    def to_metadata_payload(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=True)


class WorkspaceManifest(BaseModel):
    """Session-scoped workspace manifest."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    version: int = Field(default=1, ge=1)
    updated_at: str = Field(..., min_length=1)
    assets: list[WorkspaceAttachmentRecord] = Field(default_factory=list)
