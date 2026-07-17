"""Validation helpers for tenant-scoped long-term memory."""

from __future__ import annotations

import re
from typing import Any

MEMORY_SCOPE_PATTERN = r"^[0-9a-f]{64}$"
_MEMORY_SCOPE_RE = re.compile(MEMORY_SCOPE_PATTERN)
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


def normalize_memory_scope(
    value: Any,
    *,
    allow_none: bool = False,
) -> str | None:
    """Validate the opaque server-issued memory partition identifier."""
    if value is None:
        if allow_none:
            return None
        raise ValueError("memory_scope is required")
    if type(value) is not str or _MEMORY_SCOPE_RE.fullmatch(value) is None:
        raise ValueError("memory_scope must be exactly 64 lowercase hexadecimal characters")
    return value


def normalize_agent_name(value: Any) -> str | None:
    """Normalize the optional agent partition without allowing path components."""
    if value is None:
        return None
    if type(value) is not str or _AGENT_NAME_RE.fullmatch(value) is None:
        raise ValueError("agent_name must contain only letters, numbers, and hyphens")
    return value.lower()
