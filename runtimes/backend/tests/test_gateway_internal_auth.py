import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from src.channels.manager import ChannelManager
from src.channels.message_bus import MessageBus
from src.channels.store import ChannelStore
from src.gateway import config as gateway_config_module
from src.gateway.app import create_app
from src.gateway.internal_auth import GATEWAY_INTERNAL_TOKEN_HEADER

_TEST_TOKEN = "gateway-internal-test-token-0123456789"


@pytest.fixture(autouse=True)
def _reset_gateway_config():
    gateway_config_module._gateway_config = None
    yield
    gateway_config_module._gateway_config = None


def _configured_app(monkeypatch):
    monkeypatch.setenv("GATEWAY_INTERNAL_API_TOKEN", _TEST_TOKEN)
    app = create_app()

    @app.get("/api/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_health_is_anonymous_but_api_requires_internal_token(monkeypatch):
    with TestClient(_configured_app(monkeypatch)) as client:
        assert _TEST_TOKEN not in repr(gateway_config_module.get_gateway_config())

        assert client.get("/health").status_code == 200
        assert client.get("/api/probe").status_code == 401
        assert (
            client.get(
                "/api/probe",
                headers={GATEWAY_INTERNAL_TOKEN_HEADER: "wrong-token"},
            ).status_code
            == 401
        )

        response = client.get(
            "/api/probe",
            headers={GATEWAY_INTERNAL_TOKEN_HEADER: _TEST_TOKEN},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}


def test_duplicate_internal_token_headers_are_rejected(monkeypatch):
    client = TestClient(_configured_app(monkeypatch))

    response = client.get(
        "/api/probe",
        headers=[
            (GATEWAY_INTERNAL_TOKEN_HEADER, _TEST_TOKEN),
            (GATEWAY_INTERNAL_TOKEN_HEADER, _TEST_TOKEN),
        ],
    )

    assert response.status_code == 401


def test_api_fails_closed_without_server_token(monkeypatch):
    monkeypatch.delenv("GATEWAY_INTERNAL_API_TOKEN", raising=False)
    client = TestClient(create_app())

    response = client.get(
        "/api/models",
        headers={GATEWAY_INTERNAL_TOKEN_HEADER: "attacker-supplied-token"},
    )

    assert response.status_code == 503
    assert "attacker-supplied-token" not in response.text


def test_gateway_startup_fails_without_internal_token(monkeypatch):
    monkeypatch.delenv("GATEWAY_INTERNAL_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="GATEWAY_INTERNAL_API_TOKEN is required"):
        with TestClient(create_app()):
            pass


def test_gateway_startup_rejects_template_token(monkeypatch):
    monkeypatch.setenv(
        "GATEWAY_INTERNAL_API_TOKEN",
        "replace-with-a-strong-random-gateway-token",
    )

    with pytest.raises(RuntimeError, match="random token"):
        with TestClient(create_app()):
            pass


def test_rejected_token_is_not_logged_or_returned(monkeypatch, caplog):
    client = TestClient(_configured_app(monkeypatch))
    rejected_token = "do-not-log-this-attacker-token"

    response = client.get(
        "/api/probe",
        headers={GATEWAY_INTERNAL_TOKEN_HEADER: rejected_token},
    )

    assert response.status_code == 401
    assert rejected_token not in response.text
    assert rejected_token not in caplog.text


def test_channel_manager_authenticates_gateway_callbacks(monkeypatch, tmp_path):
    captured: dict = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"models": []}

    class _Client:
        def __init__(self, *, follow_redirects, trust_env):
            captured.update(
                follow_redirects=follow_redirects,
                trust_env=trust_env,
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers, timeout):
            captured.update(url=url, headers=headers, timeout=timeout)
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    manager = ChannelManager(
        bus=MessageBus(),
        store=ChannelStore(path=tmp_path / "channel-store.json"),
        gateway_url="http://gateway:8001",
        gateway_internal_api_token=_TEST_TOKEN,
    )

    result = asyncio.run(manager._fetch_gateway("/api/models", "models"))

    assert result == "No models configured."
    assert captured == {
        "url": "http://gateway:8001/api/models",
        "headers": {GATEWAY_INTERNAL_TOKEN_HEADER: _TEST_TOKEN},
        "timeout": 10,
        "follow_redirects": False,
        "trust_env": False,
    }
