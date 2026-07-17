"""Privacy and resource-boundary tests for direct provider HTTP calls."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests


RAG_ROOT = Path(__file__).resolve().parents[1]
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))

from core import provider_http  # noqa: E402


class FakeResponse(requests.Response):
    def __init__(
        self,
        body: bytes,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunk_size: int = 3,
    ):
        super().__init__()
        self.body = body
        self.status_code = status_code
        self.headers.update(headers or {})
        self.chunk_size = chunk_size
        self.closed = False

    def iter_content(self, chunk_size: int):
        del chunk_size
        for offset in range(0, len(self.body), self.chunk_size):
            yield self.body[offset : offset + self.chunk_size]

    def close(self):
        self.closed = True


def test_provider_session_does_not_trust_environment_proxies():
    assert provider_http._get_session().trust_env is False


def test_provider_sessions_are_not_shared_across_worker_threads():
    main_session = provider_http._get_session()
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker_session = executor.submit(provider_http._get_session).result()

    assert worker_session is not main_session
    assert worker_session.trust_env is False


def test_provider_environment_limits_reject_unbounded_values(monkeypatch):
    monkeypatch.setenv("RAG_TEST_FLOAT", "999999")
    monkeypatch.setenv("RAG_TEST_INT", str(1024**4))

    assert provider_http._bounded_float_from_env(
        "RAG_TEST_FLOAT",
        3.0,
        maximum=60.0,
    ) == 3.0
    assert provider_http._bounded_int_from_env(
        "RAG_TEST_INT",
        4096,
        maximum=8192,
    ) == 4096


def test_provider_post_enforces_transport_defaults(monkeypatch):
    response = FakeResponse(b'{"ok": true}')
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return response

    monkeypatch.setattr(provider_http._get_session(), "post", fake_post)

    result = provider_http.provider_post(
        "https://provider.invalid/v1/embed?api_key=private-key",
        json={"input": "private-document"},
    )

    assert result._content == b'{"ok": true}'
    assert result._content_consumed is True
    assert result.json() == {"ok": True}
    assert result.text == '{"ok": true}'
    assert response.closed is True
    assert captured["allow_redirects"] is False
    assert captured["stream"] is True
    assert captured["timeout"] == (
        provider_http.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        provider_http.DEFAULT_READ_TIMEOUT_SECONDS,
    )


@pytest.mark.parametrize("status_code", [302, 401, 500])
def test_provider_post_rejects_status_without_exposing_private_data(
    monkeypatch, status_code, caplog
):
    response = FakeResponse(
        b"private-provider-response-body",
        status_code=status_code,
    )
    monkeypatch.setattr(
        provider_http._get_session(),
        "post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(provider_http.ProviderHTTPError) as raised:
        provider_http.provider_post(
            "https://provider.invalid/model?api_key=private-key",
            headers={"Authorization": "Bearer private-key"},
            json={"input": "private-document"},
        )

    rendered = f"{raised.value}\n{caplog.text}"
    assert raised.value.status_code == status_code
    assert response.closed is True
    assert "private" not in rendered
    assert "provider.invalid" not in rendered


def test_provider_post_rejects_oversized_body_without_exposing_it(monkeypatch):
    response = FakeResponse(b"private-oversized-provider-body", chunk_size=4)
    monkeypatch.setattr(
        provider_http._get_session(),
        "post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(provider_http.ProviderHTTPError) as raised:
        provider_http.provider_post(
            "https://provider.invalid/v1/embed",
            max_response_bytes=8,
        )

    assert raised.value.category == "response_too_large"
    assert "private-oversized-provider-body" not in str(raised.value)
    assert response.closed is True


def test_provider_post_discards_transport_exception_details(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("private proxy and API key details")

    monkeypatch.setattr(provider_http._get_session(), "post", fail)

    with pytest.raises(provider_http.ProviderHTTPError) as raised:
        provider_http.provider_post("https://provider.invalid/?api_key=private-key")

    assert raised.value.__cause__ is None
    assert "private" not in str(raised.value)
    assert "provider.invalid" not in str(raised.value)
