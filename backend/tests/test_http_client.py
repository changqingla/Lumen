from unittest.mock import AsyncMock

import pytest

from utils import http_client as http_client_module


def test_internal_http_client_ignores_environment_proxies_and_redirects(monkeypatch):
    captured = {}
    client = object()

    def build_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(http_client_module, "internal_http_client", None)
    monkeypatch.setattr(http_client_module.httpx, "AsyncClient", build_client)

    assert http_client_module.get_internal_http_client() is client
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["timeout"] == http_client_module.settings.HTTP_DEFAULT_TIMEOUT


@pytest.mark.asyncio
async def test_close_http_client_closes_and_clears_both_clients(monkeypatch):
    external = AsyncMock()
    internal = AsyncMock()
    monkeypatch.setattr(http_client_module, "http_client", external)
    monkeypatch.setattr(http_client_module, "internal_http_client", internal)

    await http_client_module.close_http_client()

    external.aclose.assert_awaited_once_with()
    internal.aclose.assert_awaited_once_with()
    assert http_client_module.http_client is None
    assert http_client_module.internal_http_client is None
