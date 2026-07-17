from __future__ import annotations

from pathlib import Path

from src.config.app_config import AppConfig
from src.config.extensions_config import ExtensionsConfig


def test_app_config_resolve_config_path_falls_back_to_repo_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LUMEN_CONFIG_PATH", raising=False)

    resolved = AppConfig.resolve_config_path()

    expected = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
    assert resolved == expected


def test_extensions_config_has_no_tracked_deployment_state_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LUMEN_EXTENSIONS_CONFIG_PATH", raising=False)

    resolved = ExtensionsConfig.resolve_config_path()

    assert resolved is None


def test_missing_environment_config_is_an_empty_writable_deployment_target(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "extensions" / "extensions_config.json"
    monkeypatch.setenv("LUMEN_EXTENSIONS_CONFIG_PATH", str(config_path))

    assert ExtensionsConfig.resolve_config_path() == config_path
    assert ExtensionsConfig.from_file() == ExtensionsConfig(
        mcp_servers={},
        skills={},
    )
