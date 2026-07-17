import asyncio
import base64
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
import jwt
from starlette.requests import Request

from config.settings import settings
from middlewares.auth import get_current_chat_identity
from modules.auth import controller as auth_controller
from utils import security as security_module
from utils.security import (
    create_access_token,
    create_guest_token,
    decode_access_token,
    decode_guest_token,
)


_LEGACY_PYTHON_JOSE_ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJsZWdhY3ktdXNlciIsImV4cCI6NDEwMjQ0NDgwMCwiaWF0IjoxNzA0MDY3MjAwLCJ0b2tlbl90eXBlIjoiYWNjZXNzIn0."
    "eM1ICzApOLUCNlpofh9Q-OZqGkP8sdfjsaUAjFHpWKs"
)
_LEGACY_PYTHON_JOSE_KEY = "legacy-key-that-is-at-least-32-bytes-long"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/guest-session",
            "headers": [],
            "client": ("203.0.113.10", 50000),
        }
    )


def test_guest_and_access_tokens_are_type_isolated():
    guest_id = str(uuid.uuid4())
    guest_token = create_guest_token(guest_id)
    access_token = create_access_token({"sub": str(uuid.uuid4())})

    assert decode_guest_token(guest_token)["guest_id"] == guest_id
    assert decode_access_token(guest_token) is None
    assert decode_access_token(access_token)["token_type"] == "access"
    assert decode_guest_token(access_token) is None


def test_access_decoder_accepts_existing_python_jose_hs256_tokens(monkeypatch):
    """PyJWT must preserve sessions issued before the library migration."""

    monkeypatch.setattr(
        security_module.settings,
        "SECRET_KEY",
        _LEGACY_PYTHON_JOSE_KEY,
    )

    payload = decode_access_token(_LEGACY_PYTHON_JOSE_ACCESS_TOKEN)

    assert payload is not None
    assert payload["sub"] == "legacy-user"
    assert payload["token_type"] == "access"


def test_access_decoder_rejects_unsigned_tokens():
    unsigned = jwt.encode(
        {"sub": "unsigned-user", "token_type": "access"},
        key="",
        algorithm="none",
    )

    assert decode_access_token(unsigned) is None


def test_access_decoder_rejects_untyped_and_resource_tokens():
    subject = str(uuid.uuid4())
    untyped_token = jwt.encode(
        {"sub": subject},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    resource_token = jwt.encode(
        {
            "sub": subject,
            "token_type": "access",
            "purpose": "download_resource",
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    assert decode_access_token(untyped_token) is None
    assert decode_access_token(resource_token) is None


def test_access_token_signer_rejects_resource_purpose():
    with pytest.raises(ValueError, match="resource purpose"):
        create_access_token(
            {"sub": str(uuid.uuid4()), "purpose": "download_resource"}
        )


def test_guest_token_rejects_tampering_and_expiry():
    guest_token = create_guest_token(str(uuid.uuid4()))
    header, payload, encoded_signature = guest_token.split(".")
    signature = bytearray(base64.urlsafe_b64decode(f"{encoded_signature}=="))
    signature[0] ^= 0x01
    tampered_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    assert decode_guest_token(f"{header}.{payload}.{tampered_signature}") is None
    assert decode_guest_token(
        create_guest_token(str(uuid.uuid4()), expires_delta=timedelta(seconds=-1))
    ) is None


def test_guest_session_issuance_is_rate_limited_and_reuses_valid_token():
    limiter = AsyncMock()
    with patch.object(auth_controller, "enforce_auth_rate_limit", limiter):
        issued = asyncio.run(auth_controller.create_guest_session(_request(), existing_token=None))

    payload = decode_guest_token(issued["guest_token"])
    assert payload is not None
    assert issued["expires_in"] > 0
    limiter.assert_awaited_once()

    limiter.reset_mock()
    reused = asyncio.run(
        auth_controller.create_guest_session(
            _request(),
            existing_token=issued["guest_token"],
        )
    )
    assert reused["guest_token"] == issued["guest_token"]
    limiter.assert_not_awaited()


def test_guest_session_replaces_an_invalid_existing_token_under_rate_limit():
    limiter = AsyncMock()
    with patch.object(auth_controller, "enforce_auth_rate_limit", limiter):
        issued = asyncio.run(
            auth_controller.create_guest_session(
                _request(),
                existing_token="expired-or-invalid-token",
            )
        )

    assert issued["guest_token"] != "expired-or-invalid-token"
    assert decode_guest_token(issued["guest_token"]) is not None
    limiter.assert_awaited_once()


def test_chat_identity_uses_only_the_signed_guest_claim():
    guest_id = str(uuid.uuid4())
    token = create_guest_token(guest_id)
    guest_user = SimpleNamespace(id=uuid.uuid4())
    repository = MagicMock()
    repository.get_or_create_guest_user = AsyncMock(return_value=guest_user)

    with patch("repositories.user_repository.UserRepository", return_value=repository):
        identity = asyncio.run(
            get_current_chat_identity(
                guest_token=token,
                credentials=None,
                db=MagicMock(),
            )
        )

    assert identity.is_guest is True
    assert identity.guest_id == guest_id
    assert identity.user is guest_user
    repository.get_or_create_guest_user.assert_awaited_once_with(guest_id)


def test_chat_identity_rejects_an_unsigned_guest_value():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            get_current_chat_identity(
                guest_token=str(uuid.uuid4()),
                credentials=None,
                db=MagicMock(),
            )
        )

    assert exc_info.value.status_code == 401
