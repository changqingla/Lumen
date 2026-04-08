import os

os.environ.setdefault("DEBUG", "false")

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from fastapi import HTTPException

from modules.model_config.services import model_config_service as model_config_service_module
from modules.model_config.services.model_config_service import ModelConfigService
from modules.model_config.security.model_config_security import (
    create_runtime_model_binding_token,
    decode_runtime_model_binding_token,
)


def make_service():
    db = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    return ModelConfigService(db=db), db


@pytest.mark.asyncio
async def test_resolve_selected_model_embeds_thread_id_in_dynamic_token():
    user_id = uuid4()
    binding_id = uuid4()
    service, _db = make_service()
    service.binding_repo = SimpleNamespace(
        get_by_user_and_binding_name=AsyncMock(
            return_value=SimpleNamespace(
                id=binding_id,
                binding_name=f"user-model:{binding_id}",
                is_enabled=True,
            )
        )
    )

    resolved = await service.resolve_selected_model(
        user_id=user_id,
        selected_model_name=f"user-model:{binding_id}",
        runtime_models=[],
        thread_id="thread-123",
    )

    payload = decode_runtime_model_binding_token(resolved["dynamic_model_token"])
    assert payload["binding_id"] == str(binding_id)
    assert payload["user_id"] == str(user_id)
    assert payload["thread_id"] == "thread-123"


@pytest.mark.asyncio
async def test_resolve_runtime_binding_rejects_thread_mismatch():
    user_id = uuid4()
    binding_id = uuid4()
    service, _db = make_service()
    token = create_runtime_model_binding_token(
        binding_id=str(binding_id),
        user_id=str(user_id),
        thread_id="thread-a",
    )

    with pytest.raises(HTTPException, match="线程不匹配"):
        await service.resolve_runtime_binding(token, thread_id="thread-b")


@pytest.mark.asyncio
async def test_resolve_runtime_binding_rejects_invalid_identity_payload(monkeypatch):
    service, _db = make_service()

    monkeypatch.setattr(
        model_config_service_module,
        "decode_runtime_model_binding_token",
        lambda _: {
            "binding_id": "not-a-uuid",
            "user_id": "still-not-a-uuid",
        },
    )

    with pytest.raises(HTTPException, match="模型绑定令牌缺少有效标识"):
        await service.resolve_runtime_binding("broken-token", thread_id="thread-a")


@pytest.mark.asyncio
async def test_create_model_binding_rejects_inactive_credential():
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                is_active=False,
            )
        )
    )
    service.binding_repo = SimpleNamespace(list_by_user=AsyncMock(return_value=[]))

    with pytest.raises(HTTPException, match="请先填写该供应商的 API Key"):
        await service.create_model_binding(
            user_id=user_id,
            provider_code="openai",
            provider_model_name="gpt-4.1-mini",
        )


@pytest.mark.asyncio
async def test_delete_provider_credential_reports_removed_bindings_count():
    user_id = uuid4()
    service, db = make_service()
    service.binding_repo = SimpleNamespace(
        list_by_user=AsyncMock(
            return_value=[
                SimpleNamespace(provider_code="openai"),
                SimpleNamespace(provider_code="openai"),
                SimpleNamespace(provider_code="anthropic"),
            ]
        )
    )
    service.provider_repo = SimpleNamespace(
        delete_by_user_and_provider=AsyncMock(return_value=True)
    )

    result = await service.delete_provider_credential(user_id, "openai")
    db.commit.assert_awaited_once()

    assert result == {
        "provider_code": "openai",
        "provider_display_name": "OpenAI",
        "removed_bindings_count": 2,
        "success": True,
    }


@pytest.mark.asyncio
async def test_list_remote_provider_models_uses_provider_endpoint_and_merges_static_metadata(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
            )
        )
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "gpt-4.1-mini", "description": "remote description"},
                    {"id": "custom-model"},
                ]
            }

    class FakeClient:
        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    result = await service.list_remote_provider_models(user_id, "openai")

    assert result["provider_code"] == "openai"
    assert result["base_url"] == "https://api.openai.com/v1"
    assert captured["url"] == "https://api.openai.com/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer secret-key"}
    assert result["models"][0]["name"] == "gpt-4.1-mini"
    assert result["models"][0]["display_name"] == "GPT-4.1 Mini"
    assert result["models"][0]["supports_vision"] is True
    assert result["models"][1]["name"] == "custom-model"
    assert result["models"][1]["display_name"] == "custom-model"


@pytest.mark.asyncio
async def test_create_model_binding_accepts_remote_model_not_in_static_registry(monkeypatch):
    user_id = uuid4()
    credential_id = uuid4()
    service, db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                id=credential_id,
                is_active=True,
                api_key_encrypted="encrypted-key",
            )
        )
    )

    created_bindings = []

    async def fake_create(binding):
        created_bindings.append(binding)
        return binding

    service.binding_repo = SimpleNamespace(
        list_by_user=AsyncMock(return_value=[]),
        create=AsyncMock(side_effect=fake_create),
        get_by_id=AsyncMock(return_value=None),
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")
    service._fetch_remote_provider_models = AsyncMock(
        return_value=[
            {
                "name": "custom-remote-model",
                "display_name": "Custom Remote Model",
                "description": "remote only",
                "supports_vision": True,
                "supports_thinking": False,
                "supports_reasoning_effort": False,
                "provider_code": "openai",
                "provider_display_name": "OpenAI",
                "provider_icon_key": "openai",
            }
        ]
    )

    result = await service.create_model_binding(
        user_id=user_id,
        provider_code="openai",
        provider_model_name="custom-remote-model",
    )
    db.commit.assert_awaited_once()

    assert created_bindings
    assert created_bindings[0].provider_model_name == "custom-remote-model"
    assert created_bindings[0].display_name == "Custom Remote Model"
    assert created_bindings[0].supports_vision is True
    assert result["provider_model_name"] == "custom-remote-model"
    assert result["display_name"] == "Custom Remote Model"


@pytest.mark.asyncio
async def test_list_remote_provider_models_returns_static_minimax_models_without_http(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
            )
        )
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    result = await service.list_remote_provider_models(user_id, "minimax")

    assert captured["url"] == "https://api.minimaxi.com/v1/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer secret-key"}
    assert result["provider_code"] == "minimax"
    assert result["models"]
    assert result["models"][0]["name"] == "MiniMax-M2.7"


@pytest.mark.asyncio
async def test_list_remote_provider_models_probes_dashscope_coding_before_returning_static_models(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
            )
        )
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    result = await service.list_remote_provider_models(user_id, "dashscope-coding")

    assert captured["url"] == "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer secret-key"}
    assert result["provider_code"] == "dashscope-coding"
    assert result["models"]
    assert result["models"][0]["name"] == "qwen3-coder-plus"


@pytest.mark.asyncio
async def test_list_remote_provider_models_tries_later_probe_candidate_after_non_auth_failure(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
            )
        )
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    calls: list[dict[str, object]] = []

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "json": json,
                }
            )
            request = httpx.Request("POST", url)
            if len(calls) == 1:
                return httpx.Response(404, request=request)
            return httpx.Response(200, request=request)

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    result = await service.list_remote_provider_models(user_id, "minimax")

    assert result["provider_code"] == "minimax"
    assert len(calls) == 2
    assert calls[0]["url"] == "https://api.minimaxi.com/v1/chat/completions"
    assert calls[0]["headers"] == {"Authorization": "Bearer secret-key"}
    assert calls[0]["json"]["model"] == "MiniMax-M2.7"
    assert calls[1]["json"]["model"] == "MiniMax-M2.5"


@pytest.mark.asyncio
async def test_list_remote_provider_models_rejects_minimax_when_probe_fails(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
            )
        )
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    request = httpx.Request("POST", "https://api.minimaxi.com/v1/chat/completions")
    response = httpx.Response(400, request=request)

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            return response

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    with pytest.raises(HTTPException, match="拉取供应商模型列表失败"):
        await service.list_remote_provider_models(user_id, "minimax")


@pytest.mark.asyncio
async def test_list_remote_provider_models_stops_probe_immediately_on_auth_failure(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
            )
        )
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    calls: list[dict[str, object]] = []

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "json": json,
                }
            )
            request = httpx.Request("POST", url)
            return httpx.Response(401, request=request)

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    with pytest.raises(HTTPException, match="API Key 无效或没有获取模型列表的权限"):
        await service.list_remote_provider_models(user_id, "minimax")

    assert len(calls) == 1
    assert calls[0]["json"]["model"] == "MiniMax-M2.7"


@pytest.mark.asyncio
async def test_list_remote_provider_models_uses_saved_custom_base_url(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
                custom_base_url="https://custom.example.com/v1",
            )
        )
    )

    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "custom-model"}]}

    class FakeClient:
        async def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    result = await service.list_remote_provider_models(user_id, "custom")

    assert captured["url"] == "https://custom.example.com/v1/models"
    assert result["base_url"] == "https://custom.example.com/v1"
    assert result["models"][0]["name"] == "custom-model"


@pytest.mark.asyncio
async def test_save_provider_credential_requires_base_url_for_custom_provider():
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(return_value=None)
    )

    with pytest.raises(HTTPException, match="请先填写 Base URL"):
        await service.save_provider_credential(
            user_id,
            "custom",
            "secret-key",
        )


@pytest.mark.asyncio
async def test_save_provider_credential_keeps_existing_key_when_api_key_omitted():
    user_id = uuid4()
    existing = SimpleNamespace(
        api_key_encrypted="encrypted-key",
        api_key_masked="sk-****",
        is_active=True,
    )
    service, db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(return_value=existing),
        update=AsyncMock(return_value=existing),
    )

    result = await service.save_provider_credential(
        user_id,
        "openai",
        None,
    )

    service.provider_repo.update.assert_awaited_once_with(
        existing,
        custom_base_url=None,
        api_key_encrypted="encrypted-key",
        api_key_masked="sk-****",
    )
    db.commit.assert_awaited_once()
    assert result["api_key_masked"] == "sk-****"


@pytest.mark.asyncio
async def test_clear_model_health_statuses_returns_updated_count():
    user_id = uuid4()
    service, db = make_service()
    service.binding_repo = SimpleNamespace(
        clear_health_statuses=AsyncMock(return_value=3)
    )

    result = await service.clear_model_health_statuses(user_id)

    service.binding_repo.clear_health_statuses.assert_awaited_once_with(user_id)
    db.commit.assert_awaited_once()
    assert result == {
        "success": True,
        "cleared_count": 3,
    }


@pytest.mark.asyncio
async def test_preview_remote_provider_models_accepts_inline_api_key_without_saved_credential(monkeypatch):
    user_id = uuid4()
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(return_value=None)
    )

    captured: dict[str, object] = {}

    async def fake_fetch(provider, api_key, *, base_url=None):
        captured["provider"] = provider.code
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return [
            {
                "name": "gpt-4.1-mini",
                "display_name": "GPT-4.1 Mini",
                "description": None,
                "supports_vision": True,
                "supports_thinking": False,
                "supports_reasoning_effort": False,
                "provider_code": "openai",
                "provider_display_name": "OpenAI",
                "provider_icon_key": "openai",
            }
        ]

    service._fetch_remote_provider_models = AsyncMock(side_effect=fake_fetch)

    result = await service.preview_remote_provider_models(
        user_id,
        "openai",
        api_key="inline-key",
    )

    assert captured["provider"] == "openai"
    assert captured["api_key"] == "inline-key"
    assert captured["base_url"] == "https://api.openai.com/v1"
    assert result["models"][0]["name"] == "gpt-4.1-mini"


@pytest.mark.asyncio
async def test_create_model_binding_rolls_back_provider_update_when_binding_conflicts():
    user_id = uuid4()
    credential_id = uuid4()
    credential = SimpleNamespace(
        id=credential_id,
        is_active=True,
        api_key_encrypted="encrypted-key",
        api_key_masked="sk-old",
    )
    service, db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(return_value=credential),
        update=AsyncMock(return_value=credential),
    )
    service.binding_repo = SimpleNamespace(
        list_by_user=AsyncMock(return_value=[
            SimpleNamespace(provider_code="openai", provider_model_name="gpt-4.1-mini"),
        ]),
        create=AsyncMock(),
        get_by_id=AsyncMock(return_value=None),
    )

    service._fetch_remote_provider_models = AsyncMock(
        return_value=[
            {
                "name": "gpt-4.1-mini",
                "display_name": "GPT-4.1 Mini",
                "description": None,
                "supports_vision": True,
                "supports_thinking": False,
                "supports_reasoning_effort": False,
                "provider_code": "openai",
                "provider_display_name": "OpenAI",
                "provider_icon_key": "openai",
            }
        ]
    )

    with pytest.raises(HTTPException, match="该模型已添加"):
        await service.create_model_binding(
            user_id=user_id,
            provider_code="openai",
            provider_model_name="gpt-4.1-mini",
            api_key="new-key",
        )

    service.provider_repo.update.assert_awaited_once()
    service.binding_repo.create.assert_not_called()
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_provider_catalog_for_user_uses_batched_credentials_query():
    user_id = uuid4()
    service, _db = make_service()
    list_by_user = AsyncMock(
        return_value=[
            SimpleNamespace(
                provider_code="openai",
                is_active=True,
                api_key_masked="sk-****",
            ),
            SimpleNamespace(
                provider_code="custom",
                is_active=True,
                api_key_masked="ck-****",
                custom_base_url="https://custom.example.com/v1",
            ),
        ]
    )
    get_by_user_and_provider = AsyncMock(side_effect=AssertionError("should not issue per-provider queries"))
    service.provider_repo = SimpleNamespace(
        list_by_user=list_by_user,
        get_by_user_and_provider=get_by_user_and_provider,
    )

    result = await service.list_provider_catalog_for_user(user_id)

    list_by_user.assert_awaited_once_with(user_id)
    get_by_user_and_provider.assert_not_called()
    providers = {item["code"]: item for item in result}
    assert providers["openai"]["credential_configured"] is True
    assert providers["custom"]["base_url"] == "https://custom.example.com/v1"
