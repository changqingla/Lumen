#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File upload security helpers for RAG endpoints."""

from pathlib import Path

from fastapi import HTTPException, UploadFile, status


UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


def normalize_upload_filename(filename: str | None) -> str:
    """Return a safe basename or reject path-like upload names."""
    raw_name = str(filename or "").strip()
    safe_name = Path(raw_name).name
    if (
        not raw_name
        or not safe_name
        or safe_name in {".", ".."}
        or safe_name != raw_name
        or "/" in raw_name
        or "\\" in raw_name
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件名不合法",
        )
    return safe_name


def _format_size(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.0f}MB"


def _raise_file_too_large(max_bytes: int) -> None:
    raise HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"文件大小超过限制 ({_format_size(max_bytes)})",
    )


async def read_upload_file_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload in chunks and fail as soon as it exceeds max_bytes."""
    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > max_bytes:
        _raise_file_too_large(max_bytes)

    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break

        total_size += len(chunk)
        if total_size > max_bytes:
            _raise_file_too_large(max_bytes)

        chunks.append(chunk)

    return b"".join(chunks)
