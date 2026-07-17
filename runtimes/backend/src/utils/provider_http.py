"""Bounded transport for fixed third-party Runtime integrations."""

from __future__ import annotations

import math
import threading

import requests

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class ProviderHTTPError(RuntimeError):
    """Stable transport error that contains no request or response data."""

    def __init__(self, category: str, status_code: int | None = None) -> None:
        self.category = category
        self.status_code = status_code
        status = str(status_code) if status_code is not None else "unknown"
        super().__init__(
            f"Runtime provider HTTP request failed: category={category} status={status}"
        )


_SESSIONS = threading.local()


def _get_session() -> requests.Session:
    session = getattr(_SESSIONS, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        _SESSIONS.session = session
    return session


def _bounded_timeout(
    timeout: float | tuple[float, float] | None,
) -> tuple[float, float]:
    if timeout is None:
        return (DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS)

    values = timeout if isinstance(timeout, tuple) else (timeout, timeout)
    if len(values) != 2:
        return (DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS)
    try:
        connect_timeout, read_timeout = (float(value) for value in values)
    except (TypeError, ValueError):
        return (DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS)
    if not all(
        math.isfinite(value) and 0 < value <= MAX_TIMEOUT_SECONDS
        for value in (connect_timeout, read_timeout)
    ):
        return (DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS)
    return (
        min(connect_timeout, DEFAULT_CONNECT_TIMEOUT_SECONDS),
        read_timeout,
    )


def provider_post(
    url: str,
    *,
    timeout: float | tuple[float, float] | None = None,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    **kwargs,
) -> requests.Response:
    """POST without proxies or redirects and materialize one bounded response."""

    if not 0 < max_response_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError(f"max_response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")

    kwargs.pop("allow_redirects", None)
    kwargs.pop("stream", None)
    try:
        response = _get_session().post(
            url,
            timeout=_bounded_timeout(timeout),
            allow_redirects=False,
            stream=True,
            **kwargs,
        )
    except requests.RequestException as exc:
        error_response = getattr(exc, "response", None)
        status = getattr(error_response, "status_code", None)
        status_code = status if isinstance(status, int) else None
        raise ProviderHTTPError("transport", status_code) from None

    status_code = response.status_code
    if not 200 <= status_code < 300:
        response._content = b""
        response._content_consumed = True
        response.close()
        return response

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError):
            declared_size = None
        if declared_size is not None and declared_size > max_response_bytes:
            response.close()
            raise ProviderHTTPError("response_too_large", status_code) from None

    chunks: list[bytes] = []
    received = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            received += len(chunk)
            if received > max_response_bytes:
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
