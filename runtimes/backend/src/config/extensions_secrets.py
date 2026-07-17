"""Secret-preserving management helpers for Runtime extension config."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from src.config.extensions_config import ExtensionsConfig, McpServerConfig

REDACTED_SECRET = "********"


def _redact_value_map(values: Mapping[str, Any]) -> dict[str, str]:
    """Keep configuration keys visible without returning their values."""
    return {str(key): REDACTED_SECRET for key in values}


def redact_mcp_server(server: McpServerConfig | Mapping[str, Any]) -> dict[str, Any]:
    """Return one management-safe MCP server representation."""
    if isinstance(server, McpServerConfig):
        data = server.model_dump()
    elif hasattr(server, "model_dump") and callable(server.model_dump):
        data = McpServerConfig.model_validate(server.model_dump()).model_dump()
    else:
        data = McpServerConfig.model_validate(dict(server)).model_dump()

    data["env"] = _redact_value_map(data.get("env", {}))
    data["headers"] = _redact_value_map(data.get("headers", {}))
    oauth = data.get("oauth")
    if isinstance(oauth, dict):
        if oauth.get("client_secret") is not None:
            oauth["client_secret"] = REDACTED_SECRET
        if oauth.get("refresh_token") is not None:
            oauth["refresh_token"] = REDACTED_SECRET
        oauth["extra_token_params"] = _redact_value_map(
            oauth.get("extra_token_params", {})
        )
    return data


def redact_mcp_configuration(config: ExtensionsConfig) -> dict[str, dict[str, Any]]:
    """Return every MCP server with all secret-bearing values masked."""
    return {
        name: redact_mcp_server(server)
        for name, server in config.mcp_servers.items()
    }


def _restore_value_map(
    incoming: Mapping[str, Any],
    existing: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, str]:
    restored: dict[str, str] = {}
    for raw_key, raw_value in incoming.items():
        key = str(raw_key)
        value = str(raw_value)
        if value != REDACTED_SECRET:
            restored[key] = value
            continue
        if key not in existing:
            raise ValueError(
                f"Redacted {field_name} value for '{key}' has no existing value to preserve"
            )
        restored[key] = str(existing[key])
    return restored


def restore_mcp_server_secrets(
    incoming: Mapping[str, Any],
    existing: Mapping[str, Any],
) -> dict[str, Any]:
    """Restore response masks from the raw on-disk server configuration."""
    validated = McpServerConfig.model_validate(dict(incoming)).model_dump()
    restored = deepcopy(validated)

    incoming_env = validated.get("env", {})
    existing_env = existing.get("env", {})
    restored["env"] = _restore_value_map(
        incoming_env if isinstance(incoming_env, dict) else {},
        existing_env if isinstance(existing_env, dict) else {},
        field_name="environment",
    )

    incoming_headers = validated.get("headers", {})
    existing_headers = existing.get("headers", {})
    restored["headers"] = _restore_value_map(
        incoming_headers if isinstance(incoming_headers, dict) else {},
        existing_headers if isinstance(existing_headers, dict) else {},
        field_name="header",
    )

    incoming_oauth = restored.get("oauth")
    if not isinstance(incoming_oauth, dict):
        return restored

    existing_oauth = existing.get("oauth")
    if not isinstance(existing_oauth, dict):
        existing_oauth = {}
    for field_name in ("client_secret", "refresh_token"):
        if incoming_oauth.get(field_name) != REDACTED_SECRET:
            continue
        if field_name not in existing_oauth:
            raise ValueError(
                f"Redacted OAuth {field_name} has no existing value to preserve"
            )
        incoming_oauth[field_name] = existing_oauth[field_name]

    incoming_params = incoming_oauth.get("extra_token_params", {})
    existing_params = existing_oauth.get("extra_token_params", {})
    incoming_oauth["extra_token_params"] = _restore_value_map(
        incoming_params if isinstance(incoming_params, dict) else {},
        existing_params if isinstance(existing_params, dict) else {},
        field_name="OAuth parameter",
    )
    return restored
