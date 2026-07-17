from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
import requests

from src.utils import provider_http


def _response(
    *,
    status_code: int = 200,
    chunks: list[bytes] | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    response = MagicMock(status_code=status_code)
    response.headers = headers or {}
    response.iter_content.return_value = chunks or []
    return response


def test_provider_post_disables_proxy_redirects_and_streams(monkeypatch):
    session = MagicMock()
    session.post.return_value = _response(chunks=[b"bounded-response"])
    monkeypatch.setattr(provider_http, "_get_session", lambda: session)

    response = provider_http.provider_post(
        "https://provider.example/v1",
        timeout=30,
        json={"input": "private"},
        allow_redirects=True,
        stream=False,
    )

    assert response._content == b"bounded-response"
    session.post.assert_called_once_with(
        "https://provider.example/v1",
        timeout=(10.0, 30.0),
        allow_redirects=False,
        stream=True,
        json={"input": "private"},
    )
    response.close.assert_called_once()


def test_provider_post_rejects_declared_or_streamed_oversize(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(provider_http, "_get_session", lambda: session)

    declared = _response(headers={"Content-Length": "5"})
    session.post.return_value = declared
    with pytest.raises(provider_http.ProviderHTTPError) as declared_error:
        provider_http.provider_post("https://provider.example", max_response_bytes=4)
    assert declared_error.value.category == "response_too_large"
    declared.close.assert_called_once()

    streamed = _response(chunks=[b"123", b"45"])
    session.post.return_value = streamed
    with pytest.raises(provider_http.ProviderHTTPError) as streamed_error:
        provider_http.provider_post("https://provider.example", max_response_bytes=4)
    assert streamed_error.value.category == "response_too_large"
    streamed.close.assert_called_once()


def test_provider_post_returns_status_without_reading_error_body(monkeypatch):
    session = MagicMock()
    response = _response(status_code=502, chunks=[b"private-provider-body"])
    session.post.return_value = response
    monkeypatch.setattr(provider_http, "_get_session", lambda: session)

    returned = provider_http.provider_post("https://provider.example")

    assert returned.status_code == 502
    assert returned._content == b""
    response.iter_content.assert_not_called()
    response.close.assert_called_once()


def test_provider_post_raises_stable_transport_error(monkeypatch):
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("private-provider-detail")
    monkeypatch.setattr(provider_http, "_get_session", lambda: session)

    with pytest.raises(provider_http.ProviderHTTPError) as error:
        provider_http.provider_post("https://provider.example")

    assert error.value.category == "transport"
    assert "private-provider-detail" not in str(error.value)


def test_sessions_are_proxy_free_and_isolated_per_thread(monkeypatch):
    created: list[MagicMock] = []

    def make_session() -> MagicMock:
        session = MagicMock()
        session.trust_env = True
        created.append(session)
        return session

    monkeypatch.setattr(provider_http.requests, "Session", make_session)
    monkeypatch.setattr(provider_http, "_SESSIONS", threading.local())
    sessions: list[MagicMock] = []

    def collect() -> None:
        sessions.append(provider_http._get_session())

    first = threading.Thread(target=collect)
    second = threading.Thread(target=collect)
    first.start()
    second.start()
    first.join()
    second.join()

    assert len(created) == 2
    assert sessions[0] is not sessions[1]
    assert all(session.trust_env is False for session in sessions)
