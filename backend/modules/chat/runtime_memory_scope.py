"""Derive opaque Runtime memory partitions from authenticated identities."""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from config.settings import settings


_MEMORY_SCOPE_DOMAIN = b"lumen/runtime-memory-scope/v1\x00"


def derive_runtime_memory_scope(user_id: UUID | str) -> str:
    """Return a stable, opaque scope without exposing the underlying user UUID."""
    canonical_user_id = UUID(str(user_id))
    secret = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(
        secret,
        _MEMORY_SCOPE_DOMAIN + canonical_user_id.bytes,
        hashlib.sha256,
    ).hexdigest()
