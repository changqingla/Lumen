from __future__ import annotations

import json
import threading

import pytest

from src.config.extensions_config import (
    update_raw_extensions_config,
    write_raw_extensions_config,
)


def test_concurrent_updates_preserve_independent_sections(tmp_path):
    config_path = tmp_path / "extensions_config.json"
    first_inside_update = threading.Event()
    release_first_update = threading.Event()
    second_started = threading.Event()
    errors: list[BaseException] = []

    def update_mcp(config: dict) -> None:
        config["mcpServers"] = {"search": {"enabled": False}}
        first_inside_update.set()
        assert release_first_update.wait(timeout=2)

    def update_skills(config: dict) -> None:
        skills = config.setdefault("skills", {})
        skills["research"] = {"enabled": False}

    def run_update(updater, *, started: threading.Event | None = None) -> None:
        if started is not None:
            started.set()
        try:
            update_raw_extensions_config(updater, config_path)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    first = threading.Thread(target=run_update, args=(update_mcp,))
    second = threading.Thread(
        target=run_update,
        args=(update_skills,),
        kwargs={"started": second_started},
    )
    first.start()
    assert first_inside_update.wait(timeout=2)
    second.start()
    assert second_started.wait(timeout=2)
    release_first_update.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"] == {"search": {"enabled": False}}
    assert saved["skills"] == {"research": {"enabled": False}}
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_failed_update_does_not_replace_existing_config(tmp_path):
    config_path = tmp_path / "extensions_config.json"
    original = {"mcpServers": {}, "skills": {"stable": {"enabled": True}}}
    write_raw_extensions_config(original, config_path)

    def fail(config: dict) -> None:
        config["skills"] = {"lost": {"enabled": False}}
        raise RuntimeError("abort update")

    with pytest.raises(RuntimeError, match="abort update"):
        update_raw_extensions_config(fail, config_path)

    assert json.loads(config_path.read_text(encoding="utf-8")) == original
