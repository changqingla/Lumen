"""Writable cache placement for RAG deployments with read-only source mounts."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4


def get_project_cache_fallback() -> Path:
    return Path(__file__).resolve().parent.parent / ".cache"


def get_rag_cache_directory() -> Path:
    configured = os.environ.get("RAG_CACHE_DIR")
    cache_dir = Path(configured) if configured else get_project_cache_fallback()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def atomic_save_trie_cache(trie: object, destination: str | Path) -> None:
    """Publish a trie cache atomically across concurrent worker processes."""
    cache_path = Path(destination)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_name(f".{cache_path.name}.{uuid4().hex}.tmp")
    try:
        trie.save(str(temporary))
        os.replace(temporary, cache_path)
    finally:
        temporary.unlink(missing_ok=True)
