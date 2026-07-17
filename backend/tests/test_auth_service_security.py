import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.auth.services import auth_service as auth_service_module
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
async def test_send_code_register_existing_email_does_not_disclose_account():
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=object()))
    service.email_service = SimpleNamespace(send_verification_code=AsyncMock(return_value=True))

    assert await service.send_verification_code("user@example.com", "register") is True
    service.email_service.send_verification_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_code_reset_missing_email_does_not_disclose_account():
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=None))
    service.email_service = SimpleNamespace(send_verification_code=AsyncMock(return_value=True))

    assert await service.send_verification_code("user@example.com", "reset") is True
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


@pytest.mark.asyncio
async def test_upload_avatar_redacts_storage_failure(monkeypatch):
    marker = "private-storage-provider-detail"
    service = AuthService(db=object())
    service.user_repo = SimpleNamespace(update_profile=AsyncMock())
    upload = SimpleNamespace(
        content_type="image/png",
        filename="avatar.png",
        read=AsyncMock(side_effect=[b"\x89PNG\r\n\x1a\ncontent", b""]),
    )
    monkeypatch.setattr(
        auth_service_module,
        "upload_file",
        AsyncMock(side_effect=RuntimeError(marker)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.upload_avatar(uuid4(), upload)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "error": {"code": "UPLOAD_FAILED", "message": "头像上传失败"}
    }
    assert marker not in str(exc_info.value.detail)
    service.user_repo.update_profile.assert_not_awaited()
