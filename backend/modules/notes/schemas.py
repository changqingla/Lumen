"""Pydantic schemas for the notes domain."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class CreateNoteRequest(BaseModel):
    """Create note request."""

    title: str
    content: Optional[str] = ""
    folder: Optional[str] = None
    tags: list[str] = []


class UpdateNoteRequest(BaseModel):
    """Update note request."""

    title: Optional[str] = None
    content: Optional[str] = None
    folder: Optional[str] = None
    folderId: Optional[str] = None
    tags: Optional[list[str]] = None


class NoteItem(BaseModel):
    """Note item response."""

    id: str
    title: str
    content: str
    folder: str
    tags: list[str]
    updatedAt: datetime
    createdAt: datetime


class NoteFolderItem(BaseModel):
    """Note folder item."""

    id: str
    name: str
    count: int
