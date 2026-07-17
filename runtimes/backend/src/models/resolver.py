"""Resolve static and dynamic chat model specifications for the runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import get_app_config

_MODEL_RESOLVER_TOKEN_ENV = "MODEL_RESOLVER_INTERNAL_TOKEN"


def _model_resolver_internal_token() -> str:
    token = str(os.getenv(_MODEL_RESOLVER_TOKEN_ENV, ""))
    if (
        not token
        or token != token.strip()
        or not token.isascii()
        or not token.isprintable()
        or len(token) < 32
        or token.lower().startswith(
            ("change-me", "replace-with-", "example", "template", "your-")
        )
    ):
        raise ValueError(f"{_MODEL_RESOLVER_TOKEN_ENV} is not configured correctly")
    return token


@dataclass(frozen=True)
class ResolvedChatModelSpec:
    """Unified model definition consumed by runtime factories and middleware."""

    name: str
    display_name: str | None
    description: str | None
    use: str
    config: dict[str, Any]
    supports_vision: bool
    supports_thinking: bool
    supports_reasoning_effort: bool
    when_thinking_enabled: dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    enforce_outbound_endpoint_policy: bool = False


def _build_static_spec(name: str | None = None) -> ResolvedChatModelSpec:
    app_config = get_app_config()
    model_name = name or (app_config.models[0].name if app_config.models else None)
    if model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    model_config = app_config.get_model_config(model_name)
    if model_config is None:
        raise ValueError(f"Model '{model_name}' not found in config.yaml.")

    config_payload = model_config.model_dump(
        exclude_none=True,
        exclude={
            "use",
            "name",
            "display_name",
            "description",
            "supports_thinking",
            "supports_reasoning_effort",
            "when_thinking_enabled",
            "thinking",
            "supports_vision",
        },
    )
    return ResolvedChatModelSpec(
        name=model_config.name,
        display_name=model_config.display_name,
        description=model_config.description,
        use=model_config.use,
        config=config_payload,
        supports_vision=model_config.supports_vision,
        supports_thinking=model_config.supports_thinking,
        supports_reasoning_effort=model_config.supports_reasoning_effort,
        when_thinking_enabled=model_config.when_thinking_enabled,
        thinking=model_config.thinking,
    )


def _resolve_dynamic_model_spec(
    dynamic_model_token: str,
    *,
    thread_id: str | None,
) -> ResolvedChatModelSpec:
    normalized_token = str(dynamic_model_token or "").strip()
    if not normalized_token:
        raise ValueError("dynamic_model_token is required for dynamic model resolution")
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        raise ValueError("thread_id is required for dynamic model resolution")

    base_url = os.getenv("LUMEN_API_INTERNAL_URL", "http://lumen_api:13000").rstrip("/")
    internal_token = _model_resolver_internal_token()

    with httpx.Client(
        timeout=30.0,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        response = client.post(
            f"{base_url}/api/internal/runtime-model-bindings/resolve",
            json={"token": normalized_token, "thread_id": normalized_thread_id},
            headers={"X-Internal-Token": internal_token},
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Dynamic model resolution returned an invalid payload")

    spec = ResolvedChatModelSpec(
        name=str(payload.get("name") or "").strip(),
        display_name=str(payload.get("display_name") or "").strip() or None,
        description=str(payload.get("description") or "").strip() or None,
        use=str(payload.get("use") or "").strip(),
        config=dict(payload.get("config") or {}),
        supports_vision=bool(payload.get("supports_vision", False)),
        supports_thinking=bool(payload.get("supports_thinking", False)),
        supports_reasoning_effort=bool(payload.get("supports_reasoning_effort", False)),
        when_thinking_enabled=None,
        thinking=None,
        enforce_outbound_endpoint_policy=True,
    )
    if not spec.name or not spec.use:
        raise ValueError("Dynamic model resolution payload is missing required fields")

    return spec


def resolve_chat_model_spec(
    name: str | None = None,
    *,
    dynamic_model_token: str | None = None,
    thread_id: str | None = None,
) -> ResolvedChatModelSpec:
    """Resolve a runtime model spec from static config or dynamic backend binding."""

    if dynamic_model_token:
        return _resolve_dynamic_model_spec(dynamic_model_token, thread_id=thread_id)
    return _build_static_spec(name)
