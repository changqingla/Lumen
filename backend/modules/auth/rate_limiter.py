"""Redis-backed rate limiting for public authentication endpoints."""

import hashlib
import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from config.redis import get_redis_client
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthRateLimit:
    """Authentication rate-limit policy."""

    scope: str
    max_attempts: int
    window_seconds: int


def _hash_key_part(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _client_ip(request: Request) -> str:
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def _rate_limit_key(request: Request, scope: str, subject: str | None) -> str:
    ip_hash = _hash_key_part(_client_ip(request))
    subject_hash = _hash_key_part(subject or "")
    return f"auth:rate-limit:{scope}:{ip_hash}:{subject_hash}"


def _too_many_requests(retry_after: int) -> HTTPException:
    normalized_retry_after = max(1, retry_after)
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": {
                "code": "RATE_LIMITED",
                "message": "请求过于频繁，请稍后再试",
            }
        },
        headers={"Retry-After": str(normalized_retry_after)},
    )


async def enforce_auth_rate_limit(
    request: Request,
    policy: AuthRateLimit,
    subject: str | None = None,
) -> None:
    """Increment and enforce a rate-limit bucket for an auth request."""
    key = _rate_limit_key(request, policy.scope, subject)
    try:
        redis_client = await get_redis_client()
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, policy.window_seconds)

        if count > policy.max_attempts:
            ttl = await redis_client.ttl(key)
            retry_after = ttl if isinstance(ttl, int) and ttl > 0 else policy.window_seconds
            raise _too_many_requests(retry_after)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Auth rate limiting failed open for %s: %s", policy.scope, exc)


LOGIN_RATE_LIMIT = AuthRateLimit(
    scope="login",
    max_attempts=settings.AUTH_RATE_LIMIT_LOGIN_MAX,
    window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
SEND_CODE_RATE_LIMIT = AuthRateLimit(
    scope="send-code",
    max_attempts=settings.AUTH_RATE_LIMIT_SEND_CODE_MAX,
    window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
REGISTER_RATE_LIMIT = AuthRateLimit(
    scope="register",
    max_attempts=settings.AUTH_RATE_LIMIT_REGISTER_MAX,
    window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
RESET_PASSWORD_RATE_LIMIT = AuthRateLimit(
    scope="reset-password",
    max_attempts=settings.AUTH_RATE_LIMIT_RESET_PASSWORD_MAX,
    window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
