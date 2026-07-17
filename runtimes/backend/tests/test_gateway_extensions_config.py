import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.config.extensions_config import ExtensionsConfig
from src.config.extensions_secrets import REDACTED_SECRET
from src.gateway.routers import mcp, skills


def _write_extensions_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "secure": {
                        "enabled": True,
                        "type": "http",
                        "url": "https://example.test/mcp",
                        "env": {"API_TOKEN": "$MCP_API_TOKEN"},
                        "headers": {"Authorization": "$MCP_AUTH_HEADER"},
                        "oauth": {
                            "enabled": True,
                            "token_url": "https://example.test/oauth/token",
                            "client_id": "client-id",
                            "client_secret": "$MCP_CLIENT_SECRET",
                            "refresh_token": "$MCP_REFRESH_TOKEN",
                            "extra_token_params": {"assertion": "$MCP_ASSERTION"},
                        },
                    }
                },
                "skills": {"existing-skill": {"enabled": False}},
                "futureExtension": {"keep": True},
            }
        ),
        encoding="utf-8",
    )


def _set_secret_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_TOKEN", "resolved-api-token")
    monkeypatch.setenv("MCP_AUTH_HEADER", "Bearer resolved-header")
    monkeypatch.setenv("MCP_CLIENT_SECRET", "resolved-client-secret")
    monkeypatch.setenv("MCP_REFRESH_TOKEN", "resolved-refresh-token")
    monkeypatch.setenv("MCP_ASSERTION", "resolved-assertion")


def test_get_mcp_configuration_redacts_all_secret_bearing_values(monkeypatch, tmp_path):
    config_path = tmp_path / "extensions_config.json"
    _write_extensions_config(config_path)
    _set_secret_environment(monkeypatch)
    runtime_config = ExtensionsConfig.from_file(str(config_path))

    with patch.object(mcp, "get_extensions_config", return_value=runtime_config):
        result = asyncio.run(mcp.get_mcp_configuration())

    server = result.mcp_servers["secure"]
    assert server.env == {"API_TOKEN": REDACTED_SECRET}
    assert server.headers == {"Authorization": REDACTED_SECRET}
    assert server.oauth is not None
    assert server.oauth.client_secret == REDACTED_SECRET
    assert server.oauth.refresh_token == REDACTED_SECRET
    assert server.oauth.extra_token_params == {"assertion": REDACTED_SECRET}
    serialized = result.model_dump_json()
    assert "resolved-" not in serialized


def test_update_mcp_configuration_preserves_raw_secret_references(monkeypatch, tmp_path):
    config_path = tmp_path / "extensions_config.json"
    _write_extensions_config(config_path)
    _set_secret_environment(monkeypatch)
    runtime_config = ExtensionsConfig.from_file(str(config_path))
    response = mcp._config_to_response(runtime_config)
    response.mcp_servers["secure"].enabled = False
    request = mcp.McpConfigUpdateRequest(mcp_servers=response.mcp_servers)

    with patch.object(ExtensionsConfig, "resolve_config_path", return_value=config_path):
        result = asyncio.run(mcp.update_mcp_configuration(request))

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    server = saved["mcpServers"]["secure"]
    assert server["enabled"] is False
    assert server["env"] == {"API_TOKEN": "$MCP_API_TOKEN"}
    assert server["headers"] == {"Authorization": "$MCP_AUTH_HEADER"}
    assert server["oauth"]["client_secret"] == "$MCP_CLIENT_SECRET"
    assert server["oauth"]["refresh_token"] == "$MCP_REFRESH_TOKEN"
    assert server["oauth"]["extra_token_params"] == {"assertion": "$MCP_ASSERTION"}
    assert saved["skills"] == {"existing-skill": {"enabled": False}}
    assert saved["futureExtension"] == {"keep": True}
    assert "resolved-" not in config_path.read_text(encoding="utf-8")
    assert result.mcp_servers["secure"].oauth.client_secret == REDACTED_SECRET


def test_update_mcp_configuration_rejects_unresolvable_redaction(tmp_path):
    config_path = tmp_path / "extensions_config.json"
    config_path.write_text('{"mcpServers": {}, "skills": {}}', encoding="utf-8")
    request = mcp.McpConfigUpdateRequest(
        mcp_servers={
            "new": mcp.McpServerConfigResponse(
                type="stdio",
                command="example",
                env={"TOKEN": REDACTED_SECRET},
            )
        }
    )

    with (
        patch.object(ExtensionsConfig, "resolve_config_path", return_value=config_path),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(mcp.update_mcp_configuration(request))

    assert exc_info.value.status_code == 400
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"mcpServers": {}, "skills": {}}


def test_update_skill_does_not_persist_resolved_mcp_secrets(monkeypatch, tmp_path):
    config_path = tmp_path / "extensions_config.json"
    _write_extensions_config(config_path)
    _set_secret_environment(monkeypatch)
    before = SimpleNamespace(name="existing-skill", description="test", license=None, category="public", enabled=False)
    after = SimpleNamespace(name="existing-skill", description="test", license=None, category="public", enabled=True)

    with (
        patch.object(ExtensionsConfig, "resolve_config_path", return_value=config_path),
        patch.object(skills, "load_skills", side_effect=[[before], [after]]),
    ):
        result = asyncio.run(skills.update_skill("existing-skill", skills.SkillUpdateRequest(enabled=True)))

    saved_text = config_path.read_text(encoding="utf-8")
    saved = json.loads(saved_text)
    assert result.enabled is True
    assert saved["skills"]["existing-skill"] == {"enabled": True}
    assert saved["mcpServers"]["secure"]["env"]["API_TOKEN"] == "$MCP_API_TOKEN"
    assert saved["mcpServers"]["secure"]["oauth"]["client_secret"] == "$MCP_CLIENT_SECRET"
    assert saved["futureExtension"] == {"keep": True}
    assert "resolved-" not in saved_text
