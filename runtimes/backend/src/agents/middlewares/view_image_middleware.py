"""Inject bounded image data into one model request without checkpointing it."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime

from src.config.paths import get_paths
from src.utils.image_files import (
    MAX_VIEW_IMAGE_BYTES,
    MAX_VIEW_IMAGES_PER_REQUEST,
    MAX_VIEW_IMAGES_TOTAL_BYTES,
    VIEW_IMAGE_SUCCESS_MESSAGE,
    ImageFileError,
    load_image_file,
    resolve_image_path,
)


class ViewImageMiddlewareState(AgentState):
    """State schema intentionally contains no image bytes or image cache."""


class ViewImageMiddleware(AgentMiddleware[ViewImageMiddlewareState]):
    """Add successful ``view_image`` results only to the outgoing model call.

    The modified message list exists only for the duration of ``handler``. It is
    never returned as a graph update, so neither raw bytes nor data URLs enter
    ``ThreadState`` or the checkpointer.
    """

    state_schema = ViewImageMiddlewareState

    def __init__(self, *, enable_image_injection: bool = True) -> None:
        self.enable_image_injection = bool(enable_image_injection)

    @staticmethod
    def _is_legacy_checkpointed_image_message(message: Any) -> bool:
        """Identify image messages persisted by the pre-ephemeral middleware."""

        if not isinstance(message, HumanMessage) or not message.id or not isinstance(message.content, list):
            return False
        has_marker = any(
            isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").startswith(("Here are the images you've viewed:", "Here are the details of the images you've viewed:")) for block in message.content
        )
        has_data_image = any(isinstance(block, dict) and block.get("type") == "image_url" and isinstance(block.get("image_url"), dict) and str(block["image_url"].get("url") or "").startswith("data:image/") for block in message.content)
        return has_marker and has_data_image

    def _legacy_message_removals(self, state: ViewImageMiddlewareState) -> dict | None:
        removals = [RemoveMessage(id=message.id) for message in state.get("messages", []) if self._is_legacy_checkpointed_image_message(message)]
        return {"messages": removals} if removals else None

    @override
    def before_agent(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        return self._legacy_message_removals(state)

    @override
    async def abefore_agent(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        return self._legacy_message_removals(state)

    @staticmethod
    def _last_assistant_message(messages: list[Any]) -> AIMessage | None:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                return message
        return None

    @staticmethod
    def _completed_tools_after(messages: list[Any], assistant: AIMessage) -> dict[str, ToolMessage]:
        try:
            assistant_index = messages.index(assistant)
        except ValueError:
            return {}
        return {message.tool_call_id: message for message in messages[assistant_index + 1 :] if isinstance(message, ToolMessage) and message.tool_call_id}

    def _temporary_image_message(self, request: ModelRequest) -> HumanMessage | None:
        assistant = self._last_assistant_message(request.messages)
        if assistant is None or not assistant.tool_calls:
            return None

        image_calls = [call for call in assistant.tool_calls if call.get("name") == "view_image"]
        if not image_calls:
            return None
        completed = self._completed_tools_after(request.messages, assistant)
        if not all(call.get("id") in completed for call in assistant.tool_calls if call.get("id")):
            return None

        context = getattr(request.runtime, "context", None) or {}
        thread_id = str(context.get("thread_id") or "").strip()
        if not thread_id:
            return None

        content: list[str | dict] = [{"type": "text", "text": "Here are the images you've viewed:"}]
        total_bytes = 0
        included = 0
        omitted = 0
        seen_paths: set[str] = set()

        for call in image_calls:
            tool_call_id = str(call.get("id") or "")
            tool_message = completed.get(tool_call_id)
            if tool_message is None or tool_message.status == "error" or str(tool_message.content) != VIEW_IMAGE_SUCCESS_MESSAGE:
                continue
            if included >= MAX_VIEW_IMAGES_PER_REQUEST:
                omitted += 1
                continue

            args = call.get("args")
            image_path = str(args.get("image_path") or "").strip() if isinstance(args, dict) else ""
            if image_path in seen_paths:
                continue
            seen_paths.add(image_path)
            try:
                path = resolve_image_path(get_paths(), thread_id, image_path)
                loaded = load_image_file(path, max_bytes=MAX_VIEW_IMAGE_BYTES)
            except ImageFileError:
                omitted += 1
                continue
            if total_bytes + len(loaded.data) > MAX_VIEW_IMAGES_TOTAL_BYTES:
                omitted += 1
                continue

            total_bytes += len(loaded.data)
            included += 1
            encoded = base64.b64encode(loaded.data).decode("ascii")
            content.extend(
                [
                    {"type": "text", "text": f"\n- **{image_path}** ({loaded.mime_type})"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{loaded.mime_type};base64,{encoded}"},
                    },
                ]
            )

        if included == 0 and omitted == 0:
            return None
        if included == 0:
            return HumanMessage(content="The requested image data was unavailable or exceeded request limits.")
        if omitted:
            content.append(
                {
                    "type": "text",
                    "text": f"\n{omitted} additional image(s) were omitted because they were unavailable or exceeded request limits.",
                }
            )
        return HumanMessage(content=content)

    def _request_with_images(self, request: ModelRequest) -> ModelRequest:
        if not self.enable_image_injection:
            return request
        image_message = self._temporary_image_message(request)
        if image_message is None:
            return request
        return request.override(messages=[*request.messages, image_message])

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._request_with_images(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._request_with_images(request))
