from __future__ import annotations

from src.mcp import cache


def test_cache_becomes_stale_when_config_is_created(monkeypatch):
    monkeypatch.setattr(cache, "_cache_initialized", True)
    monkeypatch.setattr(cache, "_config_mtime", None)
    monkeypatch.setattr(cache, "_get_config_mtime", lambda: 123)

    assert cache._is_cache_stale() is True


def test_cache_becomes_stale_when_config_is_deleted(monkeypatch):
    monkeypatch.setattr(cache, "_cache_initialized", True)
    monkeypatch.setattr(cache, "_config_mtime", 123)
    monkeypatch.setattr(cache, "_get_config_mtime", lambda: None)

    assert cache._is_cache_stale() is True


def test_cache_is_current_when_file_state_is_unchanged(monkeypatch):
    monkeypatch.setattr(cache, "_cache_initialized", True)
    monkeypatch.setattr(cache, "_config_mtime", 123)
    monkeypatch.setattr(cache, "_get_config_mtime", lambda: 123)

    assert cache._is_cache_stale() is False
