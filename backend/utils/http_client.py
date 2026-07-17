"""Shared async HTTP client helpers for external and internal integrations."""

import asyncio
from typing import Dict

import httpx

from config.settings import settings

http_client: httpx.AsyncClient | None = None
internal_http_client: httpx.AsyncClient | None = None


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


def get_internal_http_client() -> httpx.AsyncClient:
    """Get a client that cannot route service credentials through host proxies."""
    global internal_http_client
    if internal_http_client is None:
        internal_http_client = httpx.AsyncClient(
            timeout=settings.HTTP_DEFAULT_TIMEOUT,
            trust_env=False,
            follow_redirects=False,
        )
    return internal_http_client


async def close_http_client():
    """Close shared HTTP clients on shutdown."""
    global http_client, internal_http_client
    clients = tuple(
        client for client in (http_client, internal_http_client) if client is not None
    )
    http_client = None
    internal_http_client = None
    if clients:
        await asyncio.gather(*(client.aclose() for client in clients))
