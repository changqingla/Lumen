"""Helpers for safely persisting asynchronous RAG task metadata."""

from __future__ import annotations

from typing import Any, Optional, Set
from urllib.parse import urlsplit, urlunsplit


_SENSITIVE_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "secret",
    "secret_key",
    "token",
}
_SENSITIVE_SUFFIXES = (
    "_access_key",
    "_access_token",
    "_api_key",
    "_authorization",
    "_client_secret",
    "_password",
    "_private_key",
    "_secret",
    "_secret_key",
    "_token",
)


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def is_sensitive_key(key: Any) -> bool:
    """Return whether a mapping key conventionally contains a secret."""
    normalized = _normalize_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _strip_url_credentials(value: str) -> str:
    """Remove URL userinfo while preserving the endpoint itself."""
    if "@" not in value:
        return value

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    if not parsed.netloc or "@" not in parsed.netloc:
        return value

    return urlunsplit(
        (parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, parsed.query, parsed.fragment)
    )


def _collect_string_values(value: Any) -> Set[str]:
    if isinstance(value, str):
        return {value} if len(value) >= 4 else set()
    if isinstance(value, dict):
        collected: Set[str] = set()
        for item in value.values():
            collected.update(_collect_string_values(item))
        return collected
    if isinstance(value, (list, tuple)):
        collected = set()
        for item in value:
            collected.update(_collect_string_values(item))
        return collected
    return set()


def _collect_secret_values(value: Any, *, parent_key: str = "") -> Set[str]:
    collected: Set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = _normalize_key(key)
            if is_sensitive_key(key):
                collected.update(_collect_string_values(item))
            else:
                collected.update(_collect_secret_values(item, parent_key=normalized_key))
        return collected

    if isinstance(value, (list, tuple)):
        for item in value:
            collected.update(_collect_secret_values(item, parent_key=parent_key))
        return collected

    if isinstance(value, str) and (
        parent_key.endswith("_url")
        or parent_key.endswith("_host")
        or parent_key in {"url", "host"}
    ):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return collected
        if parsed.password and len(parsed.password) >= 4:
            collected.add(parsed.password)
        if "@" in parsed.netloc:
            userinfo = parsed.netloc.rsplit("@", 1)[0]
            if len(userinfo) >= 4:
                collected.add(userinfo)

    return collected


def sanitize_task_metadata(
    value: Any,
    *,
    parent_key: str = "",
    _secret_values: Optional[Set[str]] = None,
) -> Any:
    """Return a copy of task metadata with secrets removed recursively.

    Configuration dictionaries are intentionally allow-open because parsers and
    model providers add non-secret options over time. Secret-shaped keys are
    removed at every nesting level, and credentials embedded in URL fields are
    stripped as a second line of defense.
    """
    if _secret_values is None:
        _secret_values = _collect_secret_values(value, parent_key=parent_key)

    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                continue
            sanitized[key] = sanitize_task_metadata(
                item,
                parent_key=_normalize_key(key),
                _secret_values=_secret_values,
            )
        return sanitized

    if isinstance(value, list):
        return [
            sanitize_task_metadata(
                item,
                parent_key=parent_key,
                _secret_values=_secret_values,
            )
            for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            sanitize_task_metadata(
                item,
                parent_key=parent_key,
                _secret_values=_secret_values,
            )
            for item in value
        )

    if isinstance(value, str):
        sanitized_value = value
        if (
            parent_key.endswith("_url")
            or parent_key.endswith("_host")
            or parent_key in {"url", "host"}
        ):
            sanitized_value = _strip_url_credentials(sanitized_value)
        for secret in sorted(_secret_values, key=len, reverse=True):
            sanitized_value = sanitized_value.replace(secret, "[REDACTED]")
        return sanitized_value

    return value


def contains_sensitive_task_metadata(value: Any) -> bool:
    """Return whether sanitizing metadata would change it."""
    return sanitize_task_metadata(value) != value
