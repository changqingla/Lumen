"""Tests for lead agent runtime model resolution behavior."""

from __future__ import annotations

import pytest

from src.agents.lead_agent import agent as lead_agent_module
from src.config.app_config import AppConfig
from src.config.model_config import ModelConfig
from src.config.sandbox_config import SandboxConfig
from src.models.resolver import ResolvedChatModelSpec


def _make_app_config(models: list[ModelConfig]) -> AppConfig:
    return AppConfig(
        models=models,
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )


def _make_model(name: str, *, supports_thinking: bool) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        description=None,
        use="langchain_openai:ChatOpenAI",
        model=name,
        supports_thinking=supports_thinking,
        supports_vision=False,
    )


def test_resolve_model_name_raises_for_missing_requested_model(monkeypatch):
    app_config = _make_app_config(
        [
            _make_model("default-model", supports_thinking=False),
            _make_model("other-model", supports_thinking=True),
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)

    with pytest.raises(ValueError, match="missing-model"):
        lead_agent_module._resolve_model_name("missing-model")


def test_resolve_model_name_uses_default_when_none(monkeypatch):
    app_config = _make_app_config(
        [
            _make_model("default-model", supports_thinking=False),
            _make_model("other-model", supports_thinking=True),
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)

    resolved = lead_agent_module._resolve_model_name(None)

    assert resolved == "default-model"


def test_resolve_model_name_raises_when_no_models_configured(monkeypatch):
    app_config = _make_app_config([])

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)

    with pytest.raises(
        ValueError,
        match="No chat models are configured",
    ):
        lead_agent_module._resolve_model_name("missing-model")


def test_make_lead_agent_disables_thinking_when_model_does_not_support_it(monkeypatch):
    app_config = _make_app_config([_make_model("safe-model", supports_thinking=False)])

    import src.tools as tools_module

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        lead_agent_module,
        "_build_middlewares",
        lambda config, model_name, supports_vision=False, agent_name=None: [],
    )
    monkeypatch.setattr(
        lead_agent_module,
        "resolve_chat_model_spec",
        lambda model_name, dynamic_model_token=None, thread_id=None: ResolvedChatModelSpec(
            name=model_name,
            display_name=model_name,
            description=None,
            use="langchain_openai:ChatOpenAI",
            config={"model": model_name},
            supports_vision=False,
            supports_thinking=False,
            supports_reasoning_effort=False,
        ),
    )

    captured: dict[str, object] = {}

    def _fake_create_chat_model_from_spec(spec, *, thinking_enabled, reasoning_effort=None):
        captured["name"] = spec.name
        captured["thinking_enabled"] = thinking_enabled
        captured["reasoning_effort"] = reasoning_effort
        return object()

    monkeypatch.setattr(lead_agent_module, "create_chat_model_from_spec", _fake_create_chat_model_from_spec)
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    result = lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "model_name": "safe-model",
                "thinking_enabled": True,
                "is_plan_mode": False,
                "subagent_enabled": False,
            }
        }
    )

    assert captured["name"] == "safe-model"
    assert captured["thinking_enabled"] is False
    assert result["model"] is not None


def test_make_lead_agent_omits_reasoning_effort_when_not_requested(monkeypatch):
    app_config = _make_app_config([_make_model("effort-model", supports_thinking=True)])

    import src.tools as tools_module

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        lead_agent_module,
        "_build_middlewares",
        lambda config, model_name, supports_vision=False, agent_name=None: [],
    )
    monkeypatch.setattr(
        lead_agent_module,
        "resolve_chat_model_spec",
        lambda model_name, dynamic_model_token=None, thread_id=None: ResolvedChatModelSpec(
            name=model_name,
            display_name=model_name,
            description=None,
            use="langchain_openai:ChatOpenAI",
            config={"model": model_name, "reasoning_effort": "high"},
            supports_vision=False,
            supports_thinking=True,
            supports_reasoning_effort=True,
        ),
    )

    captured: dict[str, object] = {}

    def _fake_create_chat_model_from_spec(spec, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(lead_agent_module, "create_chat_model_from_spec", _fake_create_chat_model_from_spec)
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "model_name": "effort-model",
                "thinking_enabled": True,
                "is_plan_mode": False,
                "subagent_enabled": False,
            }
        }
    )

    assert captured["thinking_enabled"] is True
    assert "reasoning_effort" not in captured


def test_make_lead_agent_passes_explicit_reasoning_effort(monkeypatch):
    app_config = _make_app_config([_make_model("effort-model", supports_thinking=True)])

    import src.tools as tools_module

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        lead_agent_module,
        "_build_middlewares",
        lambda config, model_name, supports_vision=False, agent_name=None: [],
    )
    monkeypatch.setattr(
        lead_agent_module,
        "resolve_chat_model_spec",
        lambda model_name, dynamic_model_token=None, thread_id=None: ResolvedChatModelSpec(
            name=model_name,
            display_name=model_name,
            description=None,
            use="langchain_openai:ChatOpenAI",
            config={"model": model_name, "reasoning_effort": "high"},
            supports_vision=False,
            supports_thinking=True,
            supports_reasoning_effort=True,
        ),
    )

    captured: dict[str, object] = {}

    def _fake_create_chat_model_from_spec(spec, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(lead_agent_module, "create_chat_model_from_spec", _fake_create_chat_model_from_spec)
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "model_name": "effort-model",
                "thinking_enabled": True,
                "reasoning_effort": "low",
                "is_plan_mode": False,
                "subagent_enabled": False,
            }
        }
    )

    assert captured["thinking_enabled"] is True
    assert captured["reasoning_effort"] == "low"


def test_make_lead_agent_can_disable_model_streaming(monkeypatch):
    app_config = _make_app_config([_make_model("safe-model", supports_thinking=False)])

    import src.tools as tools_module

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        lead_agent_module,
        "_build_middlewares",
        lambda config, model_name, supports_vision=False, agent_name=None: [],
    )
    monkeypatch.setattr(
        lead_agent_module,
        "resolve_chat_model_spec",
        lambda model_name, dynamic_model_token=None, thread_id=None: ResolvedChatModelSpec(
            name=model_name,
            display_name=model_name,
            description=None,
            use="langchain_openai:ChatOpenAI",
            config={"model": model_name},
            supports_vision=False,
            supports_thinking=False,
            supports_reasoning_effort=False,
        ),
    )

    captured: dict[str, object] = {}

    def _fake_create_chat_model_from_spec(spec, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(lead_agent_module, "create_chat_model_from_spec", _fake_create_chat_model_from_spec)
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    config = {
        "configurable": {
            "model_name": "safe-model",
            "thinking_enabled": False,
            "disable_model_streaming": True,
            "is_plan_mode": False,
            "subagent_enabled": False,
        }
    }

    lead_agent_module.make_lead_agent(config)

    assert captured["disable_streaming"] is True
    assert config["metadata"]["disable_model_streaming"] is True


def test_build_middlewares_uses_resolved_model_name_for_vision(monkeypatch):
    app_config = _make_app_config(
        [
            _make_model("stale-model", supports_thinking=False),
            ModelConfig(
                name="vision-model",
                display_name="vision-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="vision-model",
                supports_thinking=False,
                supports_vision=True,
            ),
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)

    middlewares = lead_agent_module._build_middlewares(
        {"configurable": {"model_name": "stale-model", "is_plan_mode": False, "subagent_enabled": False}},
        model_name="vision-model",
        supports_vision=True,
    )

    assert any(isinstance(m, lead_agent_module.ViewImageMiddleware) for m in middlewares)


def test_make_lead_agent_passes_thread_id_to_dynamic_model_resolution(monkeypatch):
    app_config = _make_app_config([_make_model("safe-model", supports_thinking=False)])

    import src.tools as tools_module

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(
        lead_agent_module,
        "_build_middlewares",
        lambda config, model_name, supports_vision=False, agent_name=None: [],
    )

    captured: dict[str, object] = {}

    def _fake_resolve_chat_model_spec(model_name, dynamic_model_token=None, thread_id=None):
        captured["model_name"] = model_name
        captured["dynamic_model_token"] = dynamic_model_token
        captured["thread_id"] = thread_id
        return ResolvedChatModelSpec(
            name=model_name,
            display_name=model_name,
            description=None,
            use="langchain_openai:ChatOpenAI",
            config={"model": model_name},
            supports_vision=False,
            supports_thinking=False,
            supports_reasoning_effort=False,
        )

    monkeypatch.setattr(lead_agent_module, "resolve_chat_model_spec", _fake_resolve_chat_model_spec)
    monkeypatch.setattr(lead_agent_module, "create_chat_model_from_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "model_name": "safe-model",
                "dynamic_model_token": "token-123",
                "thread_id": "thread-xyz",
                "thinking_enabled": False,
                "is_plan_mode": False,
                "subagent_enabled": False,
            }
        }
    )

    assert captured["dynamic_model_token"] == "token-123"
    assert captured["thread_id"] == "thread-xyz"


def test_create_todo_list_middleware_returns_instance_when_plan_mode_enabled():
    middleware = lead_agent_module._create_todo_list_middleware(True)

    assert isinstance(middleware, lead_agent_module.TodoMiddleware)
    assert "write_todos" in middleware.system_prompt
    assert "## When to Use" in middleware.tool_description


def test_build_middlewares_uses_configured_tool_loop_guard_limit(monkeypatch):
    app_config = AppConfig(
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
        models=[
            ModelConfig(
                name="vision-model",
                use="langchain_openai:ChatOpenAI",
                model="vision-model",
                supports_thinking=False,
                supports_vision=True,
            ),
        ],
        agent_loop={"max_identical_tool_calls": 5},
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)

    middlewares = lead_agent_module._build_middlewares(
        {"configurable": {"model_name": "vision-model", "is_plan_mode": False, "subagent_enabled": False}},
        model_name="vision-model",
    )

    loop_guard = next(m for m in middlewares if isinstance(m, lead_agent_module.ToolCallLoopGuardMiddleware))
    assert loop_guard.max_identical_calls == 5
