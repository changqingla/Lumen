import os
from types import SimpleNamespace
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest
from fastapi import HTTPException

from modules.creative_workshop import controller


@pytest.mark.asyncio
async def test_generate_image_requires_configured_api_key(monkeypatch):
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_API_KEY", "")

    with pytest.raises(HTTPException) as exc_info:
        await controller.generate_image(
            request=controller.ImageGenerationRequest(prompt="minimal icon"),
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"]["code"] == "IMAGE_API_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_generate_image_posts_openai_compatible_payload(monkeypatch):
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_API_KEY", "test-key")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_MODEL", "gpt-image-2")
    monkeypatch.setattr(controller.settings, "CREATIVE_WORKSHOP_IMAGE_TIMEOUT", 12.0)

    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": "ZmFrZS1pbWFnZQ=="}]}

    class _Client:
        def __init__(self, *args, **kwargs):
            calls.append(("init", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            calls.append(("post", url, headers, json))
            return _Response()

    monkeypatch.setattr(controller.httpx, "AsyncClient", _Client)

    result = await controller.generate_image(
        request=controller.ImageGenerationRequest(
            prompt="  minimal icon  ",
            size="1536x1024",
            quality="medium",
            output_format="jpeg",
            output_compression=80,
        ),
        current_user=SimpleNamespace(id=uuid4()),
    )

    assert result.b64_json == "ZmFrZS1pbWFnZQ=="
    assert result.mime_type == "image/jpeg"
    assert calls[0] == ("init", {"timeout": 12.0})
    assert calls[1][1] == "https://example.test/v1/images/generations"
    assert calls[1][2]["Authorization"] == "Bearer test-key"
    assert calls[1][3] == {
        "model": "gpt-image-2",
        "prompt": "minimal icon",
        "size": "1536x1024",
        "quality": "medium",
        "output_format": "jpeg",
        "output_compression": 80,
    }
