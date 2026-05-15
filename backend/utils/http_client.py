"""Shared async HTTP client helpers for external integrations."""

from typing import Dict

import httpx

from config.settings import settings

http_client: httpx.AsyncClient | None = None


def get_rag_internal_headers() -> Dict[str, str]:
    """Headers required by the internal RAG service."""
    token = (settings.RAG_INTERNAL_API_TOKEN or "").strip()
    if not token:
        raise RuntimeError("RAG_INTERNAL_API_TOKEN is not configured")
    return {"X-RAG-Internal-Token": token}


def get_http_client() -> httpx.AsyncClient:
    """Get or lazily create the shared HTTP client."""
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=settings.HTTP_DEFAULT_TIMEOUT)
    return http_client


async def close_http_client():
    """Close HTTP client on shutdown."""
    global http_client
    if http_client is None:
        return
    await http_client.aclose()
    http_client = None
