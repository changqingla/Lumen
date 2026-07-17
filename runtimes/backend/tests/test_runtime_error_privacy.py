"""Regression tests for stable Runtime errors and redacted logs."""

import asyncio
import importlib
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.gateway.routers import agents, skills

agent_sandbox_stub = ModuleType("agent_sandbox")
agent_sandbox_stub.Sandbox = MagicMock
sys.modules.setdefault("agent_sandbox", agent_sandbox_stub)

AioSandbox = importlib.import_module(
    "src.community.aio_sandbox.aio_sandbox"
).AioSandbox


def test_agents_failure_does_not_expose_exception_body(monkeypatch, caplog):
    marker = "provider-secret-agent-marker"
    monkeypatch.setattr(
        agents,
        "list_custom_agents",
        lambda: (_ for _ in ()).throw(RuntimeError(marker)),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(HTTPException) as exc_info:
        asyncio.run(agents.list_agents())

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to list agents"
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


def test_skills_failure_does_not_expose_exception_body(monkeypatch, caplog):
    marker = "provider-secret-skill-marker"
    monkeypatch.setattr(
        skills,
        "load_skills",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(HTTPException) as exc_info:
        asyncio.run(skills.list_skills())

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "加载技能失败"
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text


def test_aio_sandbox_command_failure_is_stable(caplog):
    marker = "sandbox-provider-secret-marker"
    sandbox = AioSandbox.__new__(AioSandbox)
    sandbox._client = SimpleNamespace(
        shell=SimpleNamespace(
            exec_command=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError(marker)
            )
        )
    )

    with caplog.at_level(logging.ERROR):
        result = sandbox.execute_command("true")

    assert result == "Error: sandbox command failed"
    assert marker not in result
    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text
