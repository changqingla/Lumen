#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File-name validation helpers for RAG upload endpoints."""

from pathlib import Path

from fastapi import HTTPException, status


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
