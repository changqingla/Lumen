"""Pydantic schemas for chunk management API."""
from pydantic import BaseModel
from typing import Optional, List


class ChunkListRequest(BaseModel):
    """Request for listing chunks."""
    index_name: str
    doc_id: Optional[str] = None
    page: int = 1
    page_size: int = 20


class ChunkSearchRequest(BaseModel):
    """Request for searching chunks."""
    index_name: str
    query: str
    doc_ids: Optional[List[str]] = None
    page: int = 1
    page_size: int = 20


class ChunkEditRequest(BaseModel):
    """Request for editing a single chunk."""
    index_name: str
    chunk_id: str
    content: Optional[str] = None
    available_int: Optional[int] = None


class ChunkBatchEditRequest(BaseModel):
    """Request for batch editing chunks."""
    index_name: str
    chunks: List[ChunkEditRequest]
