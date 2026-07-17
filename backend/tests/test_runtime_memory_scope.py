from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from middlewares.auth import AuthenticatedIdentity
from modules.chat import runtime_run_controller
from modules.chat.runtime_memory_scope import derive_runtime_memory_scope


def test_memory_scope_is_stable_opaque_and_user_specific(monkeypatch):
    monkeypatch.setattr(
        "modules.chat.runtime_memory_scope.settings.SECRET_KEY",
        "test-secret-key-that-is-at-least-thirty-two-bytes",
    )
    user_a = UUID("11111111-1111-1111-1111-111111111111")
    user_b = UUID("22222222-2222-2222-2222-222222222222")

    scope_a = derive_runtime_memory_scope(user_a)

    assert scope_a == derive_runtime_memory_scope(str(user_a))
    assert scope_a != derive_runtime_memory_scope(user_b)
    assert len(scope_a) == 64
    assert scope_a == scope_a.lower()
    assert str(user_a) not in scope_a


async def _build_payload(
    monkeypatch: pytest.MonkeyPatch,
    *,
    identity: AuthenticatedIdentity,
) -> dict:
    runtime_service = SimpleNamespace(
        list_runtime_models=AsyncMock(return_value=[{"name": "model-1"}]),
        resolve_assistant_id=AsyncMock(return_value="assistant-1"),
        build_run_request_template=lambda **kwargs: {
            "context": {
                "thread_id": kwargs["thread_id"],
                "model_name": kwargs["model_name"],
            },
            "input": {"messages": []},
        },
    )
    model_service = SimpleNamespace(
        resolve_selected_model=AsyncMock(return_value={"runtime_model_name": "model-1"})
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_validate_run_knowledge_scope",
        AsyncMock(return_value={"knowledge_scope": {"mode": "none"}}),
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_get_runtime_service",
        lambda: runtime_service,
    )
    monkeypatch.setattr(
        runtime_run_controller,
        "_create_model_config_service",
        lambda _db: model_service,
    )

    return await runtime_run_controller._build_authorized_run_payload(
        payload={
            "input": {"messages": [{"role": "user", "content": "hello"}]},
            "context": {"memory_scope": "0" * 64},
        },
        session=SimpleNamespace(config={}),
        thread_id="thread-1",
        identity=identity,
        db=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_authenticated_run_replaces_forged_memory_scope(monkeypatch):
    monkeypatch.setattr(
        "modules.chat.runtime_memory_scope.settings.SECRET_KEY",
        "test-secret-key-that-is-at-least-thirty-two-bytes",
    )
    user_id = uuid4()
    payload = await _build_payload(
        monkeypatch,
        identity=AuthenticatedIdentity(
            user=SimpleNamespace(id=user_id),
            is_guest=False,
        ),
    )

    assert payload["context"]["memory_scope"] == derive_runtime_memory_scope(user_id)
    assert payload["context"]["memory_scope"] != "0" * 64


@pytest.mark.asyncio
async def test_guest_run_drops_forged_scope_and_disables_persistent_memory(
    monkeypatch,
):
    payload = await _build_payload(
        monkeypatch,
        identity=AuthenticatedIdentity(
            user=SimpleNamespace(id=uuid4()),
            is_guest=True,
            guest_id=str(uuid4()),
        ),
    )

    assert "memory_scope" not in payload["context"]
