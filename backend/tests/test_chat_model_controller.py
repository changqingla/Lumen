import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from uuid import uuid4

import pytest

from middlewares.auth import AuthenticatedIdentity
from modules.chat import model_controller as chat_model_controller


def _identity(user_id):
    return AuthenticatedIdentity(user=SimpleNamespace(id=user_id), is_guest=False)


@pytest.mark.asyncio
async def test_list_chat_models_preserves_runtime_vision_metadata(monkeypatch):
    async def _fake_list_runtime_models():
        return [
            {
                "name": "gpt-5.4",
                "display_name": "gpt-5.4",
                "supports_vision": True,
                "supports_thinking": False,
                "supports_reasoning_effort": True,
            }
        ]

    class FakeModelConfigService:
        def __init__(self, db):
            self.db = db

        def serialize_system_model(self, item):
            return {
                "name": item["name"],
                "display_name": item["display_name"],
                "description": item.get("description"),
                "supports_vision": item.get("supports_vision", False),
                "supports_thinking": item.get("supports_thinking", False),
                "supports_reasoning_effort": item.get("supports_reasoning_effort", False),
                "provider_code": "openai",
                "provider_display_name": "OpenAI",
                "provider_icon_key": "openai",
                "source": "system",
            }

        async def list_user_model_bindings(self, user_id):
            return []

    monkeypatch.setattr(
        chat_model_controller,
        "_get_insight_runtime_service",
        lambda: SimpleNamespace(list_runtime_models=_fake_list_runtime_models),
    )
    from modules.model_config.services import model_config_service as model_config_service_module

    monkeypatch.setattr(model_config_service_module, "ModelConfigService", FakeModelConfigService)

    response = await chat_model_controller.list_chat_models(
        identity=_identity(uuid4()),
        db=object(),
    )

    assert response.default_model == "gpt-5.4"
    assert response.models[0].supports_vision is True
