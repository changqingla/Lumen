"""Tests for ephemeral, bounded image injection into model requests."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from src.agents.middlewares import view_image_middleware as middleware_module
from src.agents.middlewares.view_image_middleware import ViewImageMiddleware
from src.agents.thread_state import ThreadState
from src.config.paths import Paths
from src.utils.image_files import VIEW_IMAGE_SUCCESS_MESSAGE


def _request(messages, *, thread_id: str = "thread-1") -> ModelRequest:
    return ModelRequest(
        model=SimpleNamespace(),
        messages=messages,
        state={"messages": list(messages)},
        runtime=SimpleNamespace(context={"thread_id": thread_id}),
    )


def _view_image_messages(image_path: str = "/mnt/user-data/uploads/pixel.png") -> list:
    return [
        HumanMessage(content="Please inspect this image"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "view_image",
                    "args": {"image_path": image_path},
                    "id": "tc-1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(VIEW_IMAGE_SUCCESS_MESSAGE, tool_call_id="tc-1"),
    ]


def test_image_data_is_added_only_to_temporary_model_request(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    image_path = paths.sandbox_uploads_dir("thread-1") / "pixel.png"
    image_path.parent.mkdir(parents=True)
    image_bytes = b"bounded image bytes"
    image_path.write_bytes(image_bytes)
    monkeypatch.setattr(middleware_module, "get_paths", lambda: paths)

    messages = _view_image_messages()
    request = _request(messages)
    captured = {}
    sentinel = object()

    def handler(modified_request):
        captured["request"] = modified_request
        return sentinel

    result = ViewImageMiddleware().wrap_model_call(request, handler)

    assert result is sentinel
    temporary_messages = captured["request"].messages
    assert len(temporary_messages) == len(messages) + 1
    image_message = temporary_messages[-1]
    assert isinstance(image_message, HumanMessage)
    image_blocks = [block for block in image_message.content if block.get("type") == "image_url"]
    assert len(image_blocks) == 1
    encoded = image_blocks[0]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == image_bytes

    # Neither the original request nor graph state receives the data URL.
    assert request.messages == messages
    assert request.state == {"messages": messages}
    assert "data:image" not in repr(request.state)
    assert "viewed_images" not in ThreadState.__annotations__


@pytest.mark.asyncio
async def test_async_wrapper_uses_the_same_ephemeral_request(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    image_path = paths.sandbox_uploads_dir("thread-1") / "pixel.webp"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"webp payload")
    monkeypatch.setattr(middleware_module, "get_paths", lambda: paths)

    request = _request(_view_image_messages("/mnt/user-data/uploads/pixel.webp"))
    captured = {}

    async def handler(modified_request):
        captured["request"] = modified_request
        return "done"

    assert await ViewImageMiddleware().awrap_model_call(request, handler) == "done"
    assert len(captured["request"].messages) == len(request.messages) + 1
    assert "data:image" not in repr(request.state)


def test_failed_tool_result_never_loads_or_injects_image(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    monkeypatch.setattr(middleware_module, "get_paths", lambda: paths)
    messages = _view_image_messages()
    messages[-1] = ToolMessage("Error: unavailable", tool_call_id="tc-1", status="error")
    request = _request(messages)
    captured = {}

    def handler(modified_request):
        captured["request"] = modified_request
        return "done"

    assert ViewImageMiddleware().wrap_model_call(request, handler) == "done"
    assert captured["request"] is request


def test_per_request_image_count_is_bounded(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    uploads = paths.sandbox_uploads_dir("thread-1")
    uploads.mkdir(parents=True)
    tool_calls = []
    tool_messages = []
    for index in range(6):
        (uploads / f"image-{index}.png").write_bytes(f"image-{index}".encode())
        tool_calls.append(
            {
                "name": "view_image",
                "args": {"image_path": f"/mnt/user-data/uploads/image-{index}.png"},
                "id": f"tc-{index}",
                "type": "tool_call",
            }
        )
        tool_messages.append(ToolMessage(VIEW_IMAGE_SUCCESS_MESSAGE, tool_call_id=f"tc-{index}"))
    monkeypatch.setattr(middleware_module, "get_paths", lambda: paths)

    messages = [AIMessage(content="", tool_calls=tool_calls), *tool_messages]
    modified = ViewImageMiddleware()._request_with_images(_request(messages))
    image_message = modified.messages[-1]
    image_blocks = [block for block in image_message.content if block.get("type") == "image_url"]

    assert len(image_blocks) == 4
    assert "2 additional image(s) were omitted" in str(image_message.content[-1])


def test_per_request_total_image_bytes_are_bounded(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    uploads = paths.sandbox_uploads_dir("thread-1")
    uploads.mkdir(parents=True)
    (uploads / "first.png").write_bytes(b"1234")
    (uploads / "second.png").write_bytes(b"5678")
    monkeypatch.setattr(middleware_module, "get_paths", lambda: paths)
    monkeypatch.setattr(middleware_module, "MAX_VIEW_IMAGES_TOTAL_BYTES", 6)

    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "view_image",
                    "args": {"image_path": "/mnt/user-data/uploads/first.png"},
                    "id": "tc-1",
                    "type": "tool_call",
                },
                {
                    "name": "view_image",
                    "args": {"image_path": "/mnt/user-data/uploads/second.png"},
                    "id": "tc-2",
                    "type": "tool_call",
                },
            ],
        ),
        ToolMessage(VIEW_IMAGE_SUCCESS_MESSAGE, tool_call_id="tc-1"),
        ToolMessage(VIEW_IMAGE_SUCCESS_MESSAGE, tool_call_id="tc-2"),
    ]

    modified = ViewImageMiddleware()._request_with_images(_request(messages))
    image_message = modified.messages[-1]
    image_blocks = [block for block in image_message.content if block.get("type") == "image_url"]

    assert len(image_blocks) == 1
    assert "1 additional image(s) were omitted" in str(image_message.content[-1])


def test_legacy_checkpointed_data_url_message_is_removed_from_current_state():
    legacy = HumanMessage(
        id="legacy-image-message",
        content=[
            {"type": "text", "text": "Here are the images you've viewed:"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,c2VjcmV0"}},
        ],
    )
    normal = HumanMessage(id="normal-message", content="Here are the images you've viewed:")

    update = ViewImageMiddleware().before_agent(
        {"messages": [normal, legacy]},
        SimpleNamespace(),
    )

    assert update is not None
    assert len(update["messages"]) == 1
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == "legacy-image-message"
