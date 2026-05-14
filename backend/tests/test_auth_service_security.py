import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.auth.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_login_does_not_reveal_whether_email_exists():
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc_info:
        await service.login("user@example.com", "password123")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"]["message"] == "邮箱或密码错误"


@pytest.mark.asyncio
async def test_send_code_register_existing_email_returns_conflict():
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=object()))
    service.email_service = SimpleNamespace(send_verification_code=AsyncMock(return_value=True))

    with pytest.raises(HTTPException) as exc_info:
        await service.send_verification_code("user@example.com", "register")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"]["code"] == "CONFLICT"
    service.email_service.send_verification_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_code_reset_missing_email_returns_not_found():
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=None))
    service.email_service = SimpleNamespace(send_verification_code=AsyncMock(return_value=True))

    with pytest.raises(HTTPException) as exc_info:
        await service.send_verification_code("user@example.com", "reset")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"]["code"] == "NOT_FOUND"
    service.email_service.send_verification_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_avatar_rejects_svg_content_type():
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(update_profile=AsyncMock())
    upload = SimpleNamespace(
        content_type="image/svg+xml",
        filename="avatar.svg",
        read=AsyncMock(return_value=b"<svg></svg>"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.upload_avatar(uuid4(), upload)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "INVALID_FILE_TYPE"
    upload.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_avatar_rejects_mismatched_image_bytes():
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(update_profile=AsyncMock())
    upload = SimpleNamespace(
        content_type="image/png",
        filename="avatar.png",
        read=AsyncMock(side_effect=[b"<svg></svg>", b""]),
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.upload_avatar(uuid4(), upload)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "INVALID_FILE_TYPE"
