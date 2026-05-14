#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internal authentication helpers for the RAG service."""

import secrets

from fastapi import Header, HTTPException, status

from config import settings


async def require_internal_token(
    x_rag_internal_token: str | None = Header(default=None, alias="X-RAG-Internal-Token"),
) -> None:
    """Require the shared internal token for non-health RAG endpoints."""
    expected_token = (settings.RAG_INTERNAL_API_TOKEN or "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RAG_INTERNAL_API_TOKEN 未配置",
        )

    provided_token = (x_rag_internal_token or "").strip()
    if not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized internal request",
        )
