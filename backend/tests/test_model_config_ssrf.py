import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from config.settings import Settings
from modules.model_config.security.model_config_security import create_runtime_model_binding_token
from modules.model_config.services import model_config_service as model_config_service_module
from modules.model_config.services.model_config_service import ModelConfigService
from utils.outbound_endpoint_policy import (
    OUTBOUND_ENDPOINT_ERROR_MESSAGE,
    OutboundEndpointPolicy,
)


def test_private_endpoint_escape_hatch_defaults_to_disabled():
    assert Settings.model_fields["MODEL_PROVIDER_ALLOW_PRIVATE_ENDPOINTS"].default is False


async def resolve_public_endpoint(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def resolve_private_endpoint(_host: str, _port: int) -> tuple[str, ...]:
    return ("10.20.30.40",)


def make_service(*, endpoint_policy: OutboundEndpointPolicy | None = None):
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    policy = endpoint_policy or OutboundEndpointPolicy(resolver=resolve_public_endpoint)
    return ModelConfigService(db=db, endpoint_policy=policy), db


def make_custom_binding(base_url: str):
    binding_id = uuid4()
    return SimpleNamespace(
        id=binding_id,
        binding_name=f"user-model:{binding_id}",
        display_name="Custom Model",
        description=None,
        provider_code="custom",
        provider_model_name="custom-model",
        supports_vision=False,
        supports_thinking=False,
        supports_reasoning_effort=False,
        is_enabled=True,
        health_status="unknown",
        last_health_checked_at=None,
        last_health_latency_ms=None,
        last_health_error=None,
        provider_credential=SimpleNamespace(
            is_active=True,
            api_key_encrypted="encrypted-key",
            custom_base_url=base_url,
        ),
    )


@pytest.mark.asyncio
async def test_save_custom_provider_rejects_private_dns_before_persisting():
    policy = OutboundEndpointPolicy(resolver=resolve_private_endpoint)
    service, db = make_service(endpoint_policy=policy)
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(),
        create=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.save_provider_credential(
            uuid4(),
            "custom",
            "secret-key",
            base_url="http://models.internal/v1",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == OUTBOUND_ENDPOINT_ERROR_MESSAGE
    assert "10.20.30.40" not in str(exc_info.value.detail)
    service.provider_repo.get_by_user_and_provider.assert_not_awaited()
    service.provider_repo.create.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_custom_provider_models_revalidates_saved_dns(monkeypatch):
    service, _db = make_service(
        endpoint_policy=OutboundEndpointPolicy(resolver=resolve_private_endpoint)
    )
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
                custom_base_url="https://models.internal/v1",
            )
        )
    )
    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    class FakeClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("unsafe destination must not receive a request")

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await service.list_remote_provider_models(uuid4(), "custom")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == OUTBOUND_ENDPOINT_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_model_health_check_revalidates_custom_provider_dns(monkeypatch):
    binding = make_custom_binding("https://models.internal/v1")
    service, db = make_service(
        endpoint_policy=OutboundEndpointPolicy(resolver=resolve_private_endpoint)
    )

    async def update_health_status(_binding_id, _user_id, **kwargs):
        binding.health_status = kwargs["health_status"]
        binding.last_health_checked_at = kwargs["checked_at"]
        binding.last_health_latency_ms = kwargs["latency_ms"]
        binding.last_health_error = kwargs["error_message"]

    service.binding_repo = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=binding),
        update_health_status=AsyncMock(side_effect=update_health_status),
    )
    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")

    class FakeClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("unsafe destination must not receive a request")

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    result = await service.run_model_binding_health_check(uuid4(), binding.id)

    update_call = service.binding_repo.update_health_status.await_args
    assert update_call.kwargs["health_status"] == "unhealthy"
    assert update_call.kwargs["error_message"] == OUTBOUND_ENDPOINT_ERROR_MESSAGE
    assert result["health_status"] == "unhealthy"
    assert result["last_health_error"] == OUTBOUND_ENDPOINT_ERROR_MESSAGE
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_model_resolution_revalidates_before_chat_completions(monkeypatch):
    user_id = uuid4()
    binding = make_custom_binding("https://models.internal/v1")
    service, _db = make_service(
        endpoint_policy=OutboundEndpointPolicy(resolver=resolve_private_endpoint)
    )
    service.binding_repo = SimpleNamespace(get_by_id_for_user=AsyncMock(return_value=binding))
    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")
    token = create_runtime_model_binding_token(
        binding_id=str(binding.id),
        user_id=str(user_id),
        thread_id="thread-a",
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.resolve_runtime_binding(token, thread_id="thread-a")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == OUTBOUND_ENDPOINT_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_custom_provider_model_list_does_not_follow_redirect(monkeypatch):
    service, _db = make_service()
    service.provider_repo = SimpleNamespace(
        get_by_user_and_provider=AsyncMock(
            return_value=SimpleNamespace(
                api_key_encrypted="encrypted-key",
                is_active=True,
                custom_base_url="https://models.example.com/v1",
            )
        )
    )
    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def get(self, url, *, follow_redirects=None, **kwargs):
            calls.append({"url": url, "follow_redirects": follow_redirects, **kwargs})
            request = httpx.Request("GET", url)
            return httpx.Response(
                302,
                headers={"Location": "http://127.0.0.1/latest/meta-data"},
                request=request,
            )

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    with pytest.raises(HTTPException, match="拉取供应商模型列表失败"):
        await service.list_remote_provider_models(uuid4(), "custom")

    assert len(calls) == 1
    assert calls[0]["follow_redirects"] is False


@pytest.mark.asyncio
async def test_custom_health_chat_completion_does_not_follow_redirect(monkeypatch):
    binding = make_custom_binding("https://models.example.com/v1")
    service, db = make_service()

    async def update_health_status(_binding_id, _user_id, **kwargs):
        binding.health_status = kwargs["health_status"]
        binding.last_health_error = kwargs["error_message"]

    service.binding_repo = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=binding),
        update_health_status=AsyncMock(side_effect=update_health_status),
    )
    monkeypatch.setattr(model_config_service_module, "decrypt_api_key", lambda _: "secret-key")
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def post(self, url, *, follow_redirects=None, **kwargs):
            calls.append({"url": url, "follow_redirects": follow_redirects, **kwargs})
            request = httpx.Request("POST", url)
            return httpx.Response(
                302,
                headers={"Location": "http://169.254.169.254/latest/meta-data"},
                request=request,
            )

    monkeypatch.setattr(model_config_service_module, "get_http_client", lambda: FakeClient())

    result = await service.run_model_binding_health_check(uuid4(), binding.id)

    assert len(calls) == 1
    assert calls[0]["url"] == "https://models.example.com/v1/chat/completions"
    assert calls[0]["follow_redirects"] is False
    assert result["health_status"] == "unhealthy"
    assert result["last_health_error"] == "模型健康检测失败"
    db.commit.assert_awaited_once()
