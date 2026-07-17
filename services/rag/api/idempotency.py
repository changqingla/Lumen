"""Stable request identities for durable RAG document parsing."""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DOMAIN = "lumen-rag-parse-v1"


def build_document_parse_idempotency_key(
    document_id: str,
    content_sha256: str,
) -> str:
    """Bind one document identifier to the exact submitted Markdown bytes."""
    normalized_document_id = str(UUID(str(document_id).strip()))
    normalized_digest = str(content_sha256).strip().lower()
    if _SHA256_RE.fullmatch(normalized_digest) is None:
        raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
    identity = f"{_DOMAIN}\0{normalized_document_id}\0{normalized_digest}"
    return hashlib.sha256(identity.encode("ascii")).hexdigest()


__all__ = ["build_document_parse_idempotency_key"]
