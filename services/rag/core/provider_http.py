"""Bounded HTTP transport for direct RAG model-provider calls."""

from __future__ import annotations

import math
import os
import threading

import requests


def _bounded_float_from_env(
    name: str,
    default: float,
    *,
    maximum: float,
) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and 0 < value <= maximum else default


def _bounded_int_from_env(
    name: str,
    default: int,
    *,
    maximum: int,
) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if 0 < value <= maximum else default


DEFAULT_CONNECT_TIMEOUT_SECONDS = _bounded_float_from_env(
    "RAG_PROVIDER_CONNECT_TIMEOUT_SECONDS",
    10.0,
    maximum=60.0,
)
DEFAULT_READ_TIMEOUT_SECONDS = _bounded_float_from_env(
    "RAG_PROVIDER_READ_TIMEOUT_SECONDS",
    120.0,
    maximum=600.0,
)
DEFAULT_MAX_RESPONSE_BYTES = _bounded_int_from_env(
    "RAG_PROVIDER_MAX_RESPONSE_BYTES",
    16 * 1024 * 1024,
    maximum=256 * 1024 * 1024,
)


class ProviderHTTPError(RuntimeError):
    """Stable provider transport error that contains no request or response data."""

    def __init__(self, category: str, status_code: int | None = None):
        self.category = category
        self.status_code = status_code
        status = str(status_code) if status_code is not None else "unknown"
        super().__init__(f"RAG provider HTTP request failed: category={category} status={status}")


_SESSIONS = threading.local()


def _get_session() -> requests.Session:
    """Return one connection pool per worker thread; requests.Session is not thread-safe."""
    session = getattr(_SESSIONS, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        _SESSIONS.session = session
    return session


def provider_post(
    url: str,
    *,
    timeout: float | tuple[float, float] | None = None,
    max_response_bytes: int | None = None,
    **kwargs,
) -> requests.Response:
    """POST to a provider with bounded, non-redirecting, proxy-free transport."""

    request_timeout = (
        timeout
        if timeout is not None
        else (
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
            DEFAULT_READ_TIMEOUT_SECONDS,
        )
    )
    response_limit = (
        max_response_bytes
        if max_response_bytes is not None
        else DEFAULT_MAX_RESPONSE_BYTES
    )
    if response_limit <= 0:
        raise ValueError("max_response_bytes must be positive")

    # These guarantees cannot be relaxed by an individual provider adapter.
    kwargs.pop("allow_redirects", None)
    kwargs.pop("stream", None)

    try:
        response = _get_session().post(
            url,
            timeout=request_timeout,
            allow_redirects=False,
            stream=True,
            **kwargs,
        )
    except requests.RequestException as error:
        error_response = getattr(error, "response", None)
        response_status = getattr(error_response, "status_code", None)
        status_code = response_status if isinstance(response_status, int) else None
        raise ProviderHTTPError("transport", status_code) from None

    status_code = response.status_code
    if not 200 <= status_code < 300:
        response.close()
        raise ProviderHTTPError("status", status_code) from None

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > response_limit:
            response.close()
            raise ProviderHTTPError("response_too_large", status_code) from None

    chunks: list[bytes] = []
    received = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            received += len(chunk)
            if received > response_limit:
                raise ProviderHTTPError("response_too_large", status_code)
            chunks.append(chunk)
    except ProviderHTTPError:
        response.close()
        raise
    except requests.RequestException:
        response.close()
        raise ProviderHTTPError("response_read", status_code) from None

    response._content = b"".join(chunks)
    response._content_consumed = True
    response.close()
    return response
