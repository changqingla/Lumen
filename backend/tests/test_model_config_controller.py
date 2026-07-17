import os

os.environ["DEBUG"] = "false"

from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.model_config import controller as model_config_controller


@pytest.mark.asyncio
async def test_get_model_config_catalog_does_not_depend_on_runtime_models(monkeypatch):
    captured: dict[str, object] = {}
    user_id = uuid4()

    class FakeModelConfigService:
        async def list_model_config_page(self, user_id):
            captured["user_id"] = user_id
            return {
                "providers": [
                    {
                        "code": "openai",
                        "display_name": "OpenAI",
                        "description": "OpenAI models",
                        "icon_key": "openai",
                        "api_key_label": "API Key",
                        "base_url": "https://api.openai.com/v1",
                        "credential_configured": False,
                        "api_key_masked": None,
                        "models": [],
                    }
                ],
                "user_models": [],
            }

    monkeypatch.setattr(
        model_config_controller,
        "_create_model_config_service",
        lambda db: FakeModelConfigService(),
    )

    response = await model_config_controller.get_model_config_catalog(
        current_user=SimpleNamespace(id=user_id),
        db=object(),
    )

    assert captured["user_id"] == user_id
    payload = response.model_dump()
    assert "providers" in payload
    assert "user_models" in payload
    assert "system_models" not in payload
