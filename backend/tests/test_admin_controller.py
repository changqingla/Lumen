import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from datetime import datetime

import pytest
from fastapi import HTTPException

from modules.admin import controller as admin_controller
from modules.organization.repositories import (
    organization_repository as organization_repository_module,
)
from schemas.schemas import GenerateActivationCodeRequest


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_activation_code_admin_endpoints_require_admin():
    current_user = SimpleNamespace(id=uuid4(), is_admin=False)
    request = GenerateActivationCodeRequest(type="member", duration_days=30, max_usage=1)

    with pytest.raises(HTTPException) as exc_info:
        await admin_controller.generate_activation_code(
            request=request,
            current_user=current_user,
            db=object(),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["message"] == "仅管理员可以生成激活码"

    with pytest.raises(HTTPException) as exc_info:
        await admin_controller.list_activation_codes(
            current_user=current_user,
            db=object(),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["message"] == "仅管理员可以查看激活码列表"

    with pytest.raises(HTTPException) as exc_info:
        await admin_controller.deactivate_activation_code(
            code="SAMPLECODE123",
            current_user=current_user,
            db=object(),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["message"] == "仅管理员可以作废激活码"

    with pytest.raises(HTTPException) as exc_info:
        await admin_controller.batch_generate_activation_codes(
            request=request,
            count=2,
            current_user=current_user,
            db=object(),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["message"] == "仅管理员可以生成激活码"


@pytest.mark.asyncio
async def test_get_statistics_calculates_average_members_from_database(monkeypatch):
    current_user = SimpleNamespace(id=uuid4(), is_admin=True)
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult(9),   # total users
                _ScalarResult(4),   # explorers
                _ScalarResult(3),   # members
                _ScalarResult(1),   # premium members
                _ScalarResult(1),   # admins
                _ScalarResult(5),   # total organization memberships
                _ScalarResult(15),  # total knowledge bases
                _ScalarResult(1),   # public knowledge bases
                _ScalarResult(0),   # organization-shared knowledge bases
            ]
        )
    )

    monkeypatch.setattr(
        organization_repository_module.OrganizationRepository,
        "count_all",
        AsyncMock(return_value=2),
    )

    result = await admin_controller.get_statistics(
        current_user=current_user,
        db=db,
    )

    assert result == {
        "users": {
            "total": 9,
            "explorers": 4,
            "members": 3,
            "advanced_members": 1,
            "admins": 1,
        },
        "organizations": {
            "total": 2,
            "average_members": 2.5,
        },
        "knowledge_bases": {
            "total": 15,
            "public": 1,
            "shared": 0,
        },
    }


@pytest.mark.asyncio
async def test_list_users_includes_last_active_and_billing_cycle_quota():
    user_id = uuid4()
    current_user = SimpleNamespace(id=uuid4(), is_admin=True)
    fake_user = SimpleNamespace(
        id=user_id,
        name="Alice",
        email="alice@example.com",
        avatar=None,
        user_level="member",
        is_admin=False,
        created_at=None,
    )

    last_active_at = datetime(2026, 3, 15, 8, 30, 0)
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarResult(1),
                _RowsResult([(fake_user, last_active_at, 12345)]),
            ]
        )
    )

    response = await admin_controller.list_users(
        page=1,
        page_size=20,
        current_user=current_user,
        db=db,
    )

    assert response["total"] == 1
    assert response["items"] == [
        {
            "id": str(user_id),
            "name": "Alice",
            "email": "alice@example.com",
            "avatar": None,
            "user_level": "member",
            "is_admin": False,
            "created_at": None,
            "last_active_at": "2026-03-15T08:30:00",
            "billing_cycle_token_total": 12345,
            "model_quota_limit": 5_000_000,
            "quota_reset_date": response["items"][0]["quota_reset_date"],
        }
    ]
