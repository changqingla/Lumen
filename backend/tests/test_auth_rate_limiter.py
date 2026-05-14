import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from modules.auth import controller as auth_controller
from modules.auth import rate_limiter
from modules.auth.rate_limiter import AuthRateLimit
from modules.auth.schemas import LoginRequest


class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, 60)


def _request(ip: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [(b"x-real-ip", ip.encode("ascii"))],
            "client": ("127.0.0.1", 12345),
        }
    )


def _direct_request(
    peer_ip: str = "8.8.8.8",
    forwarded_ip: str = "203.0.113.99",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [(b"x-forwarded-for", forwarded_ip.encode("ascii"))],
            "client": (peer_ip, 12345),
        }
    )


@pytest.mark.asyncio
async def test_auth_rate_limiter_blocks_after_limit(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(rate_limiter, "get_redis_client", AsyncMock(return_value=fake_redis))
    policy = AuthRateLimit(scope="login-test", max_attempts=2, window_seconds=60)

    await rate_limiter.enforce_auth_rate_limit(_request(), policy, "user@example.com")
    await rate_limiter.enforce_auth_rate_limit(_request(), policy, "user@example.com")

    with pytest.raises(HTTPException) as exc_info:
        await rate_limiter.enforce_auth_rate_limit(_request(), policy, "user@example.com")

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}
    assert exc_info.value.detail["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_auth_rate_limiter_blocks_after_ip_limit_across_subjects(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(rate_limiter, "get_redis_client", AsyncMock(return_value=fake_redis))
    policy = AuthRateLimit(
        scope="login-ip-test",
        max_attempts=10,
        window_seconds=60,
        ip_max_attempts=2,
    )

    await rate_limiter.enforce_auth_rate_limit(_request(), policy, "a@example.com")
    await rate_limiter.enforce_auth_rate_limit(_request(), policy, "b@example.com")

    with pytest.raises(HTTPException) as exc_info:
        await rate_limiter.enforce_auth_rate_limit(_request(), policy, "c@example.com")

    assert exc_info.value.status_code == 429


def test_client_ip_ignores_forwarded_for_from_public_direct_clients():
    assert rate_limiter._client_ip(_direct_request()) == "8.8.8.8"


@pytest.mark.asyncio
async def test_login_route_checks_rate_limit_before_auth_service(monkeypatch):
    enforce = AsyncMock(side_effect=HTTPException(status_code=429, detail="limited"))
    service = SimpleNamespace(login=AsyncMock())
    monkeypatch.setattr(auth_controller, "enforce_auth_rate_limit", enforce)
    monkeypatch.setattr(auth_controller, "_create_auth_service", lambda db: service)

    with pytest.raises(HTTPException) as exc_info:
        await auth_controller.login(
            http_request=_request(),
            request=LoginRequest(email="user@example.com", password="password123"),
            db=object(),
        )

    assert exc_info.value.status_code == 429
    enforce.assert_awaited_once()
    service.login.assert_not_awaited()
