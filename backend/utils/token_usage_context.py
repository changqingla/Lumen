"""Short-lived signed identities for Runtime usage reports and durable queue items."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import jwt
from jwt import InvalidTokenError

from config.settings import settings


_TOKEN_TYPE = "runtime_usage"
_QUEUE_KEY_LABEL = b"lumen/token-usage-queue/v2"


class InvalidUsageContext(ValueError):
    """Raised when a Runtime usage credential is missing, forged, or expired."""


@dataclass(frozen=True, slots=True)
class UsageContextClaims:
    reservation_id: UUID
    user_id: UUID
    session_id: UUID
    window_start: datetime
    window_end: datetime
    expires_at: datetime

    def to_queue_dict(self) -> dict[str, str]:
        return {
            "reservation_id": str(self.reservation_id),
            "user_id": str(self.user_id),
            "session_id": str(self.session_id),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_queue_dict(cls, payload: dict[str, Any]) -> "UsageContextClaims":
        try:
            return cls(
                reservation_id=UUID(str(payload["reservation_id"])),
                user_id=UUID(str(payload["user_id"])),
                session_id=UUID(str(payload["session_id"])),
                window_start=_parse_aware_datetime(payload["window_start"]),
                window_end=_parse_aware_datetime(payload["window_end"]),
                expires_at=_parse_aware_datetime(payload["expires_at"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise InvalidUsageContext("Malformed queued usage context") from exc


def _parse_aware_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed.astimezone(timezone.utc)


def create_usage_context(claims: UsageContextClaims, *, issued_at: datetime | None = None) -> str:
    """Sign a run-scoped credential without exposing the signing key to Runtime."""

    now = (issued_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return jwt.encode(
        {
            "token_type": _TOKEN_TYPE,
            "jti": str(claims.reservation_id),
            "sub": str(claims.user_id),
            "session_id": str(claims.session_id),
            "window_start": int(claims.window_start.timestamp()),
            "window_end": int(claims.window_end.timestamp()),
            "iat": now,
            "exp": claims.expires_at,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_usage_context(token: str) -> UsageContextClaims:
    """Verify a Runtime credential and normalize all identity-bearing claims."""

    try:
        payload = jwt.decode(
            str(token or "").strip(),
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        if payload.get("token_type") != _TOKEN_TYPE:
            raise InvalidUsageContext("Unexpected token type")
        claims = UsageContextClaims(
            reservation_id=UUID(str(payload.get("jti") or "")),
            user_id=UUID(str(payload.get("sub") or "")),
            session_id=UUID(str(payload.get("session_id") or "")),
            window_start=datetime.fromtimestamp(int(payload["window_start"]), tz=timezone.utc),
            window_end=datetime.fromtimestamp(int(payload["window_end"]), tz=timezone.utc),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
        )
        if claims.window_start >= claims.window_end:
            raise InvalidUsageContext("Invalid billing window")
        return claims
    except InvalidUsageContext:
        raise
    except (
        InvalidTokenError,
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise InvalidUsageContext("Invalid or expired usage context") from exc


def _queue_signing_key() -> bytes:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        _QUEUE_KEY_LABEL,
        hashlib.sha256,
    ).digest()


def canonical_queue_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sign_queue_payload(payload: dict[str, Any]) -> str:
    """Authenticate accepted queue data so direct Redis writes cannot forge usage."""

    return hmac.new(
        _queue_signing_key(),
        canonical_queue_payload(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_queue_payload(payload: dict[str, Any], signature: str) -> bool:
    expected = sign_queue_payload(payload)
    return hmac.compare_digest(expected, str(signature or ""))
