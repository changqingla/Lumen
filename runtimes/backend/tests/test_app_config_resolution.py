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


def test_extensions_config_resolve_config_path_falls_back_to_repo_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LUMEN_EXTENSIONS_CONFIG_PATH", raising=False)

    resolved = ExtensionsConfig.resolve_config_path()

    expected = Path(__file__).resolve().parents[2] / "config" / "extensions_config.json"
    assert resolved == expected
