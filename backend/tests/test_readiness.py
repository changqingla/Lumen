import os
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DEBUG"] = "false"

import pytest
from fastapi.responses import JSONResponse

from app import main


@pytest.mark.asyncio
async def test_global_exception_handler_never_exposes_debug_detail(
    monkeypatch,
    caplog,
):
    marker = "private-database-password-detail"
    monkeypatch.setattr(main.settings, "DEBUG", True)

    with caplog.at_level(logging.ERROR):
        response = await main.global_exception_handler(
            SimpleNamespace(),
            RuntimeError(marker),
        )

    assert response.status_code == 500
    assert json.loads(response.body) == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
        }
    }
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_readiness_failure_log_redacts_exception_detail(monkeypatch, caplog):
    marker = "private-postgresql-connection-detail"

    class FailingSession:
        async def __aenter__(self):
            raise RuntimeError(marker)

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(main, "AsyncSessionLocal", FailingSession)

    with caplog.at_level(logging.WARNING):
        result = await main._probe_database()

    assert result == {"name": "postgresql", "ok": False}
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_readiness_checks_all_critical_dependencies(monkeypatch):
    monkeypatch.setattr(main, "_probe_database", AsyncMock(return_value={"name": "postgresql", "ok": True}))
    monkeypatch.setattr(main, "_probe_redis", AsyncMock(return_value={"name": "redis", "ok": True}))
    monkeypatch.setattr(main, "_probe_minio", AsyncMock(return_value={"name": "minio", "ok": True}))

    checked_names: list[str] = []

    async def probe_http(name: str, _url: str, _timeout: float):
        checked_names.append(name)
        return {"name": name, "ok": True}

    monkeypatch.setattr(main, "_probe_dependency", probe_http)

    result = await main.readiness_check()

    assert result["status"] == "ready"
    assert [item["name"] for item in result["dependencies"]] == [
        "postgresql",
        "redis",
        "minio",
        "rag",
        "gateway",
        "langgraph",
    ]
    assert checked_names == ["rag", "gateway", "langgraph"]


@pytest.mark.asyncio
async def test_readiness_returns_sanitized_degraded_response(monkeypatch):
    monkeypatch.setattr(
        main,
        "_probe_database",
        AsyncMock(return_value={"name": "postgresql", "ok": False}),
    )
    monkeypatch.setattr(main, "_probe_redis", AsyncMock(return_value={"name": "redis", "ok": True}))
    monkeypatch.setattr(main, "_probe_minio", AsyncMock(return_value={"name": "minio", "ok": True}))
    monkeypatch.setattr(
        main,
        "_probe_dependency",
        AsyncMock(side_effect=lambda name, _url, _timeout: {"name": name, "ok": True}),
    )

    result = await main.readiness_check()

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    assert b"postgresql" in result.body
    assert b"http://" not in result.body
    assert b"error" not in result.body


@pytest.mark.asyncio
async def test_dependency_probe_ignores_proxy_environment_and_redirects(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    result = await main._probe_dependency("gateway", "http://gateway/health", 2.0)

    assert result == {"name": "gateway", "ok": True}
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
