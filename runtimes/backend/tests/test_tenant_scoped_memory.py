from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import TypeAdapter, ValidationError

from src.agents.lead_agent.prompt import _get_memory_context
from src.agents.memory.queue import MemoryUpdateQueue
from src.agents.memory.scope import normalize_agent_name, normalize_memory_scope
from src.agents.memory.updater import (
    MemoryUpdater,
    _create_empty_memory,
    _get_memory_file_path,
    _save_memory_to_file,
    get_memory_data,
)
from src.agents.middlewares.memory_middleware import MemoryMiddleware
from src.agents.middlewares.scoped_memory_prompt_middleware import (
    ScopedMemoryPromptMiddleware,
)
from src.agents.runtime_context import RuntimeContext
from src.client import InsightFlowClient
from src.config.memory_config import MemoryConfig
from src.config.paths import Paths

SCOPE_A = "a" * 64
SCOPE_B = "b" * 64


@pytest.fixture
def scoped_storage(tmp_path):
    import src.agents.memory.updater as updater

    updater._memory_cache.clear()
    updater._path_locks.clear()
    with (
        patch.object(updater, "get_paths", return_value=Paths(tmp_path)),
        patch.object(
            updater,
            "get_memory_config",
            return_value=MemoryConfig(storage_path=""),
        ),
    ):
        yield tmp_path
    updater._memory_cache.clear()
    updater._path_locks.clear()


def _profile(summary: str) -> dict:
    profile = _create_empty_memory()
    profile["user"]["workContext"]["summary"] = summary
    return profile


def test_user_and_agent_partitions_never_share_files(scoped_storage):
    assert _save_memory_to_file(_profile("tenant-a"), SCOPE_A)
    assert _save_memory_to_file(_profile("tenant-b"), SCOPE_B)
    assert _save_memory_to_file(_profile("tenant-a-agent"), SCOPE_A, "researcher")

    assert get_memory_data(SCOPE_A)["user"]["workContext"]["summary"] == "tenant-a"
    assert get_memory_data(SCOPE_B)["user"]["workContext"]["summary"] == "tenant-b"
    assert get_memory_data(SCOPE_A, "researcher")["user"]["workContext"]["summary"] == "tenant-a-agent"
    assert not list(scoped_storage.rglob("*.tmp"))


def test_separate_processes_write_different_scopes_independently(scoped_storage):
    runtime_root = Path(__file__).resolve().parent.parent
    environment = os.environ.copy()
    environment["LUMEN_HOME"] = str(scoped_storage)
    environment["PYTHONPATH"] = str(runtime_root)
    script = """
import sys
from src.agents.memory.updater import _create_empty_memory, _save_memory_to_file
profile = _create_empty_memory()
profile["user"]["workContext"]["summary"] = sys.argv[2]
raise SystemExit(0 if _save_memory_to_file(profile, sys.argv[1]) else 1)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, scope, summary],
            cwd=runtime_root,
            env=environment,
        )
        for scope, summary in (
            (SCOPE_A, "process-a"),
            (SCOPE_B, "process-b"),
        )
    ]

    assert [process.wait(timeout=10) for process in processes] == [0, 0]
    assert get_memory_data(SCOPE_A)["user"]["workContext"]["summary"] == "process-a"
    assert get_memory_data(SCOPE_B)["user"]["workContext"]["summary"] == "process-b"


def test_memory_updater_reads_and_writes_only_its_scope(scoped_storage):
    model = MagicMock()
    model.invoke.side_effect = [
        SimpleNamespace(
            content=json.dumps(
                {
                    "user": {
                        "workContext": {
                            "shouldUpdate": True,
                            "summary": "alpha-updated",
                        }
                    }
                }
            )
        ),
        SimpleNamespace(
            content=json.dumps(
                {
                    "user": {
                        "workContext": {
                            "shouldUpdate": True,
                            "summary": "beta-updated",
                        }
                    }
                }
            )
        ),
    ]
    updater = MemoryUpdater()
    messages = [HumanMessage(content="hello"), AIMessage(content="hi")]

    with patch.object(updater, "_get_model", return_value=model):
        assert updater.update_memory(
            messages,
            memory_scope=SCOPE_A,
            thread_id="thread-a",
        )
        assert updater.update_memory(
            messages,
            memory_scope=SCOPE_B,
            thread_id="thread-b",
        )

    assert get_memory_data(SCOPE_A)["user"]["workContext"]["summary"] == "alpha-updated"
    assert get_memory_data(SCOPE_B)["user"]["workContext"]["summary"] == "beta-updated"


def test_legacy_global_memory_is_never_loaded(scoped_storage):
    legacy = scoped_storage / "memory.json"
    legacy.write_text(json.dumps(_profile("legacy-global-secret")), encoding="utf-8")

    scoped = get_memory_data(SCOPE_A)

    assert scoped["user"]["workContext"]["summary"] == ""
    assert "legacy-global-secret" not in json.dumps(scoped)
    assert legacy.exists()


def test_missing_scope_disables_memory_instead_of_falling_back(scoped_storage):
    (scoped_storage / "memory.json").write_text(
        json.dumps(_profile("must-not-load")),
        encoding="utf-8",
    )

    assert get_memory_data(None)["user"]["workContext"]["summary"] == ""
    assert _save_memory_to_file(_profile("must-not-save"), None) is False


@pytest.mark.parametrize(
    "scope",
    ["A" * 64, "a" * 63, "../" + "a" * 61, "a" * 65, 123],
)
def test_invalid_scope_is_rejected(scope):
    with pytest.raises(ValueError):
        normalize_memory_scope(scope)


def test_runtime_context_schema_enforces_lowercase_fixed_length_scope():
    adapter = TypeAdapter(RuntimeContext)

    assert adapter.validate_python({"memory_scope": SCOPE_A})["memory_scope"] == SCOPE_A
    for invalid in ("A" * 64, "a" * 63, "../" + "a" * 61, 1):
        with pytest.raises(ValidationError):
            adapter.validate_python({"memory_scope": invalid})


@pytest.mark.parametrize("agent_name", ["../admin", "a/b", ".", "", "a" * 65])
def test_agent_name_cannot_traverse(agent_name):
    with pytest.raises(ValueError):
        normalize_agent_name(agent_name)


def test_configured_relative_storage_cannot_escape_home(tmp_path):
    import src.agents.memory.updater as updater

    with (
        patch.object(updater, "get_paths", return_value=Paths(tmp_path)),
        patch.object(
            updater,
            "get_memory_config",
            return_value=MemoryConfig(storage_path="../escape.json"),
        ),
        pytest.raises(ValueError),
    ):
        _get_memory_file_path(SCOPE_A)


def test_memory_file_symlink_is_rejected(scoped_storage, tmp_path):
    path = _get_memory_file_path(SCOPE_A)
    path.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_profile("outside-secret")), encoding="utf-8")
    path.symlink_to(outside)

    with pytest.raises(ValueError):
        get_memory_data(SCOPE_A)


def test_prompt_injection_reads_only_the_requested_scope(scoped_storage):
    assert _save_memory_to_file(_profile("alpha-private"), SCOPE_A)
    assert _save_memory_to_file(_profile("beta-private"), SCOPE_B)

    prompt_a = _get_memory_context(memory_scope=SCOPE_A)
    prompt_b = _get_memory_context(memory_scope=SCOPE_B)

    assert "alpha-private" in prompt_a
    assert "beta-private" not in prompt_a
    assert "beta-private" in prompt_b
    assert "alpha-private" not in prompt_b
    assert _get_memory_context(memory_scope=None) == ""


def test_prompt_middleware_resolves_scope_for_each_model_call(monkeypatch):
    captured: list[tuple[str, str | None]] = []

    def get_context(*, memory_scope, agent_name=None):
        captured.append((memory_scope, agent_name))
        return f"<memory>{memory_scope}</memory>"

    monkeypatch.setattr(
        "src.agents.middlewares.scoped_memory_prompt_middleware._get_memory_context",
        get_context,
    )
    middleware = ScopedMemoryPromptMiddleware(agent_name="Researcher")

    request_a = ModelRequest(
        model=MagicMock(),
        messages=[],
        system_prompt="base prompt",
        runtime=SimpleNamespace(context={"memory_scope": SCOPE_A}),
    )
    request_b = ModelRequest(
        model=MagicMock(),
        messages=[],
        system_prompt="base prompt",
        runtime=SimpleNamespace(context={"memory_scope": SCOPE_B}),
    )

    injected_a = middleware._inject(request_a)
    injected_b = middleware._inject(request_b)

    assert SCOPE_A in injected_a.system_prompt
    assert SCOPE_B not in injected_a.system_prompt
    assert SCOPE_B in injected_b.system_prompt
    assert SCOPE_A not in injected_b.system_prompt
    assert captured == [(SCOPE_A, "researcher"), (SCOPE_B, "researcher")]


def test_queue_debounces_by_scope_agent_and_thread():
    queue = MemoryUpdateQueue()
    with patch.object(queue, "_reset_timer"):
        queue.add(
            "same-thread",
            [HumanMessage(content="a")],
            memory_scope=SCOPE_A,
        )
        queue.add(
            "same-thread",
            [HumanMessage(content="b")],
            memory_scope=SCOPE_B,
        )
        queue.add(
            "same-thread",
            [HumanMessage(content="a-agent")],
            memory_scope=SCOPE_A,
            agent_name="researcher",
        )
        queue.add(
            "same-thread",
            [HumanMessage(content="latest-a")],
            memory_scope=SCOPE_A,
        )

    assert len(queue._queue) == 3
    assert {(item.memory_scope, item.agent_name, item.thread_id) for item in queue._queue} == {
        (SCOPE_A, None, "same-thread"),
        (SCOPE_B, None, "same-thread"),
        (SCOPE_A, "researcher", "same-thread"),
    }


def test_memory_middleware_skips_guest_and_rejects_malformed_scope():
    middleware = MemoryMiddleware()
    state = {"messages": [HumanMessage(content="hello"), AIMessage(content="hi")]}
    queue = MagicMock()

    with patch(
        "src.agents.middlewares.memory_middleware.get_memory_queue",
        return_value=queue,
    ):
        assert (
            middleware.after_agent(
                state,
                SimpleNamespace(context={"thread_id": "thread-1"}),
            )
            is None
        )
        queue.add.assert_not_called()

        middleware.after_agent(
            state,
            SimpleNamespace(context={"thread_id": "thread-1", "memory_scope": SCOPE_A}),
        )
        queue.add.assert_called_once()
        assert queue.add.call_args.kwargs["memory_scope"] == SCOPE_A

        with pytest.raises(ValueError):
            middleware.before_agent(
                state,
                SimpleNamespace(context={"thread_id": "thread-1", "memory_scope": "../bad"}),
            )


def test_gateway_memory_api_requires_explicit_valid_scope(monkeypatch):
    from src.gateway.routers import memory as memory_router

    monkeypatch.setattr(
        memory_router,
        "get_memory_data",
        lambda memory_scope, agent_name=None: _profile(f"{memory_scope}:{agent_name or 'default'}"),
    )
    app = FastAPI()
    app.include_router(memory_router.router)
    client = TestClient(app)

    assert client.get("/api/memory").status_code == 422
    assert client.get("/api/memory", params={"memory_scope": "A" * 64}).status_code == 422
    response = client.get(
        "/api/memory",
        params={"memory_scope": SCOPE_A, "agent_name": "researcher"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["workContext"]["summary"] == (f"{SCOPE_A}:researcher")


def test_embedded_client_carries_only_constructor_scope():
    client = InsightFlowClient(memory_scope=SCOPE_A)

    config = client._get_runnable_config(
        "thread-1",
        memory_scope=SCOPE_B,
    )

    assert config["configurable"]["memory_scope"] == SCOPE_A
    with pytest.raises(ValueError):
        InsightFlowClient(memory_scope="../invalid")
