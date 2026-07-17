#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internal authentication helpers for the RAG service."""

import secrets

from fastapi import Header, HTTPException, status

from config import settings

_MIN_INTERNAL_TOKEN_LENGTH = 32
_TEMPLATE_PREFIXES = ("change-me", "replace-with-", "example", "template", "your-")


def validate_rag_internal_token(value: str | None) -> str:
    """Reject missing, weak, non-ASCII, or template service credentials."""
    token = str(value or "")
    if (
        not token
        or token != token.strip()
        or not token.isascii()
        or not token.isprintable()
        or len(token) < _MIN_INTERNAL_TOKEN_LENGTH
        or token.lower().startswith(_TEMPLATE_PREFIXES)
    ):
        raise RuntimeError(
            "RAG_INTERNAL_API_TOKEN must be a random printable ASCII token of "
            f"at least {_MIN_INTERNAL_TOKEN_LENGTH} characters"
        )
    return token


async def require_internal_token(
    x_rag_internal_token: list[str] | None = Header(
        default=None,
        alias="X-RAG-Internal-Token",
    ),
) -> None:
    """Require the shared internal token for non-health RAG endpoints."""
    try:
        expected_token = validate_rag_internal_token(settings.RAG_INTERNAL_API_TOKEN)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG internal authentication is unavailable",
        ) from exc

    supplied = x_rag_internal_token or []
    if len(supplied) != 1 or not secrets.compare_digest(supplied[0], expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized internal request",
        )
