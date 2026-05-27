"""Tests for src.models.factory.create_chat_model."""

from __future__ import annotations

import pytest
from langchain.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from src.config.app_config import AppConfig
from src.config.model_config import ModelConfig
from src.config.sandbox_config import SandboxConfig
from src.models import factory as factory_module
from src.models.resolver import ResolvedChatModelSpec, dump_resolved_chat_model_spec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_config(models: list[ModelConfig]) -> AppConfig:
    return AppConfig(
        models=models,
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )


def _make_model(
    name: str = "test-model",
    *,
    use: str = "langchain_openai:ChatOpenAI",
    supports_thinking: bool = False,
    supports_reasoning_effort: bool = False,
    when_thinking_enabled: dict | None = None,
    thinking: dict | None = None,
    **extra,
) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        description=None,
        use=use,
        model=name,
        supports_thinking=supports_thinking,
        supports_reasoning_effort=supports_reasoning_effort,
        when_thinking_enabled=when_thinking_enabled,
        thinking=thinking,
        supports_vision=False,
        **extra,
    )


class FakeChatModel(BaseChatModel):
    """Minimal BaseChatModel stub that records the kwargs it was called with."""

    captured_kwargs: dict = {}

    def __init__(self, **kwargs):
        # Store kwargs before pydantic processes them
        FakeChatModel.captured_kwargs = dict(kwargs)
        super().__init__(**kwargs)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError

    def _stream(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError


def _patch_factory(monkeypatch, app_config: AppConfig, model_class=FakeChatModel):
    """Patch get_app_config, resolve_class, and tracing for isolated unit tests."""
    monkeypatch.setattr(factory_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: model_class)
    monkeypatch.setattr(factory_module, "is_tracing_enabled", lambda: False)
    monkeypatch.setattr(factory_module, "_get_supported_model_config_keys", lambda cls: None)
    monkeypatch.setattr(
        factory_module,
        "resolve_chat_model_spec",
        lambda name=None, dynamic_model_token=None, thread_id=None: _build_spec_from_app_config(app_config, name),
    )


def _build_spec_from_app_config(app_config: AppConfig, name: str | None) -> ResolvedChatModelSpec:
    model_name = name or app_config.models[0].name
    model_config = app_config.get_model_config(model_name)
    if model_config is None:
        raise ValueError(f"Model '{model_name}' not found in config.yaml.")

    config_payload = model_config.model_dump(
        exclude_none=True,
        exclude={
            "use",
            "name",
            "display_name",
            "description",
            "supports_thinking",
            "supports_reasoning_effort",
            "when_thinking_enabled",
            "thinking",
            "supports_vision",
        },
    )
    return ResolvedChatModelSpec(
        name=model_config.name,
        display_name=model_config.display_name,
        description=model_config.description,
        use=model_config.use,
        config=config_payload,
        supports_vision=model_config.supports_vision,
        supports_thinking=model_config.supports_thinking,
        supports_reasoning_effort=model_config.supports_reasoning_effort,
        when_thinking_enabled=model_config.when_thinking_enabled,
        thinking=model_config.thinking,
    )


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_uses_first_model_when_name_is_none(monkeypatch):
    cfg = _make_app_config([_make_model("alpha"), _make_model("beta")])
    _patch_factory(monkeypatch, cfg)

    FakeChatModel.captured_kwargs = {}
    factory_module.create_chat_model(name=None)

    # resolve_class is called — if we reach here without ValueError, the correct model was used
    assert FakeChatModel.captured_kwargs.get("model") == "alpha"


def test_raises_when_model_not_found(monkeypatch):
    cfg = _make_app_config([_make_model("only-model")])
    monkeypatch.setattr(factory_module, "get_app_config", lambda: cfg)
    monkeypatch.setattr(factory_module, "is_tracing_enabled", lambda: False)
    monkeypatch.setattr(
        factory_module,
        "resolve_chat_model_spec",
        lambda name=None, dynamic_model_token=None, thread_id=None: _build_spec_from_app_config(cfg, name),
    )

    with pytest.raises(ValueError, match="ghost-model"):
        factory_module.create_chat_model(name="ghost-model")


# ---------------------------------------------------------------------------
# thinking_enabled=True
# ---------------------------------------------------------------------------


def test_thinking_enabled_raises_when_not_supported_but_when_thinking_enabled_is_set(monkeypatch):
    """
    """
    wte = {"thinking": {"type": "enabled", "budget_tokens": 5000}}
    cfg = _make_app_config([_make_model("no-think", supports_thinking=False, when_thinking_enabled=wte)])
    _patch_factory(monkeypatch, cfg)

    with pytest.raises(ValueError, match="does not support thinking"):
        factory_module.create_chat_model(name="no-think", thinking_enabled=True)


def test_thinking_enabled_raises_for_empty_when_thinking_enabled_explicitly_set(monkeypatch):
    """
    the user explicitly provided the section, so the guard must still fire even though
    """
    cfg = _make_app_config([_make_model("no-think-empty", supports_thinking=False, when_thinking_enabled={})])
    _patch_factory(monkeypatch, cfg)

    with pytest.raises(ValueError, match="does not support thinking"):
        factory_module.create_chat_model(name="no-think-empty", thinking_enabled=True)


def test_thinking_enabled_merges_when_thinking_enabled_settings(monkeypatch):
    wte = {"temperature": 1.0, "max_tokens": 16000}
    cfg = _make_app_config([_make_model("thinker", supports_thinking=True, when_thinking_enabled=wte)])
    _patch_factory(monkeypatch, cfg)

    FakeChatModel.captured_kwargs = {}
    factory_module.create_chat_model(name="thinker", thinking_enabled=True)

    assert FakeChatModel.captured_kwargs.get("temperature") == 1.0
    assert FakeChatModel.captured_kwargs.get("max_tokens") == 16000


# ---------------------------------------------------------------------------
# thinking_enabled=False — disable logic
# ---------------------------------------------------------------------------


def test_thinking_disabled_openai_gateway_format(monkeypatch):
    """
    """
    wte = {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 10000}}}
    cfg = _make_app_config(
        [
            _make_model(
                "openai-gw",
                supports_thinking=True,
                supports_reasoning_effort=True,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="openai-gw", thinking_enabled=False)

    assert captured.get("extra_body") == {"thinking": {"type": "disabled"}}
    assert captured.get("reasoning_effort") == "minimal"
    assert "thinking" not in captured  # must NOT set the direct thinking param


def test_thinking_disabled_langchain_anthropic_format(monkeypatch):
    """
    """
    wte = {"thinking": {"type": "enabled", "budget_tokens": 8000}}
    cfg = _make_app_config(
        [
            _make_model(
                "anthropic-native",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                supports_reasoning_effort=False,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="anthropic-native", thinking_enabled=False)

    assert captured.get("thinking") == {"type": "disabled"}
    assert "extra_body" not in captured
    # reasoning_effort must be cleared (supports_reasoning_effort=False)
    assert captured.get("reasoning_effort") is None


def test_thinking_disabled_no_when_thinking_enabled_does_nothing(monkeypatch):
    """If when_thinking_enabled is not set, disabling thinking must not inject any kwargs."""
    cfg = _make_app_config([_make_model("plain", supports_thinking=True, when_thinking_enabled=None)])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="plain", thinking_enabled=False)

    assert "extra_body" not in captured
    assert "thinking" not in captured
    # reasoning_effort not forced (supports_reasoning_effort defaults to False → cleared)
    assert captured.get("reasoning_effort") is None


# ---------------------------------------------------------------------------
# reasoning_effort stripping
# ---------------------------------------------------------------------------


def test_reasoning_effort_cleared_when_not_supported(monkeypatch):
    cfg = _make_app_config([_make_model("no-effort", supports_reasoning_effort=False)])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="no-effort", thinking_enabled=False)

    assert captured.get("reasoning_effort") is None


def test_reasoning_effort_preserved_when_supported(monkeypatch):
    wte = {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 5000}}}
    cfg = _make_app_config(
        [
            _make_model(
                "effort-model",
                supports_thinking=True,
                supports_reasoning_effort=True,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="effort-model", thinking_enabled=False)

    # When supports_reasoning_effort=True, it should NOT be cleared to None
    # The disable path sets it to "minimal"; supports_reasoning_effort=True keeps it
    assert captured.get("reasoning_effort") == "minimal"


def test_runtime_overrides_take_precedence_over_model_config(monkeypatch):
    cfg = _make_app_config([
        _make_model(
            "override-model",
            supports_reasoning_effort=True,
            temperature=0.2,
            reasoning_effort="low",
        )
    ])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(
        name="override-model",
        thinking_enabled=False,
        temperature=0.8,
        reasoning_effort="high",
    )

    assert captured.get("temperature") == 0.8
    assert captured.get("reasoning_effort") == "high"


def test_openai_compatible_models_use_lumen_user_agent_by_default(monkeypatch):
    cfg = _make_app_config([_make_model("openai-compatible")])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="openai-compatible")

    assert captured.get("default_headers") == {"User-Agent": "Lumen/1.0"}


def test_patched_openai_compatible_models_use_lumen_user_agent_by_default(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "patched-openai-compatible",
                use="src.models.patched_openai:PatchedChatOpenAI",
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="patched-openai-compatible")

    assert captured.get("default_headers") == {"User-Agent": "Lumen/1.0"}


def test_openai_compatible_models_preserve_configured_user_agent(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "openai-compatible",
                default_headers={
                    "User-Agent": "CustomGateway/2.0",
                    "X-Gateway": "custom",
                },
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="openai-compatible")

    assert captured.get("default_headers") == {
        "User-Agent": "CustomGateway/2.0",
        "X-Gateway": "custom",
    }


def test_openai_compatible_models_merge_default_headers_without_user_agent(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "openai-compatible",
                default_headers={"X-Gateway": "custom"},
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="openai-compatible")

    assert captured.get("default_headers") == {
        "X-Gateway": "custom",
        "User-Agent": "Lumen/1.0",
    }


def test_unsupported_model_config_keys_are_filtered(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "filtered-model",
                temperature=0.3,
                connect_timeout=10,
                max_context_tokens=256000,
                compression_threshold=0.7,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)
    monkeypatch.setattr(factory_module, "_get_supported_model_config_keys", lambda cls: {"model", "temperature"})

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="filtered-model")

    assert captured.get("model") == "filtered-model"
    assert captured.get("temperature") == 0.3
    assert "connect_timeout" not in captured
    assert "max_context_tokens" not in captured
    assert "compression_threshold" not in captured


def test_openai_max_tokens_are_clamped_to_conservative_limit(monkeypatch, caplog):
    cfg = _make_app_config([_make_model("clamped-model", max_tokens=256000)])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    with caplog.at_level("WARNING", logger=factory_module.__name__):
        factory_module.create_chat_model(name="clamped-model")

    assert captured.get("max_tokens") == 65536
    assert "Clamping max_tokens for model 'clamped-model' from config to 65536" in caplog.text


def test_invalid_max_tokens_is_removed(monkeypatch, caplog):
    cfg = _make_app_config([_make_model("invalid-model", max_tokens=0)])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    with caplog.at_level("WARNING", logger=factory_module.__name__):
        factory_module.create_chat_model(name="invalid-model")

    assert "max_tokens" not in captured
    assert "Ignoring invalid max_tokens=0 for model 'invalid-model' from config" in caplog.text


def test_request_payload_is_logged_with_user_content(monkeypatch, caplog):
    cfg = _make_app_config([_make_model("logged-model", max_tokens=1234)])
    _patch_factory(monkeypatch, cfg)

    class PayloadLoggingModel(FakeChatModel):
        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            return {
                "model": FakeChatModel.captured_kwargs.get("model"),
                "messages": [
                    {"role": getattr(message, "type", type(message).__name__), "content": getattr(message, "content", None)}
                    for message in input_
                ],
                "max_completion_tokens": FakeChatModel.captured_kwargs.get("max_tokens"),
                **kwargs,
            }

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: PayloadLoggingModel)

    with caplog.at_level("INFO", logger=factory_module.__name__):
        model = factory_module.create_chat_model(name="logged-model", thinking_enabled=False)
        model._get_request_payload(
            [HumanMessage(content="你好")],
            headers={"Authorization": "Bearer secret-token", "X-Trace": "trace-1"},
            tools=[{"type": "function", "function": {"name": "demo"}}],
        )

    assert "LLM request payload (config=logged-model, provider_model=logged-model)" in caplog.text
    assert '"content": "你好"' in caplog.text
    assert '"max_completion_tokens": 1234' in caplog.text
    assert '"Authorization": "***REDACTED***"' in caplog.text
    assert '"X-Trace": "trace-1"' in caplog.text


# ---------------------------------------------------------------------------
# thinking shortcut field
# ---------------------------------------------------------------------------


def test_thinking_shortcut_enables_thinking_when_thinking_enabled(monkeypatch):
    """thinking shortcut alone should act as when_thinking_enabled with a `thinking` key."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    cfg = _make_app_config(
        [
            _make_model(
                "shortcut-model",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                thinking=thinking_settings,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="shortcut-model", thinking_enabled=True)

    assert captured.get("thinking") == thinking_settings


def test_thinking_shortcut_disables_thinking_when_thinking_disabled(monkeypatch):
    """thinking shortcut should participate in the disable path (langchain_anthropic format)."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    cfg = _make_app_config(
        [
            _make_model(
                "shortcut-disable",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                supports_reasoning_effort=False,
                thinking=thinking_settings,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="shortcut-disable", thinking_enabled=False)

    assert captured.get("thinking") == {"type": "disabled"}
    assert "extra_body" not in captured


def test_thinking_shortcut_merges_with_when_thinking_enabled(monkeypatch):
    """thinking shortcut should be merged into when_thinking_enabled when both are provided."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    wte = {"max_tokens": 16000}
    cfg = _make_app_config(
        [
            _make_model(
                "merge-model",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                thinking=thinking_settings,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="merge-model", thinking_enabled=True)

    # Both the thinking shortcut and when_thinking_enabled settings should be applied
    assert captured.get("thinking") == thinking_settings
    assert captured.get("max_tokens") == 16000


def test_thinking_shortcut_not_leaked_into_model_when_disabled(monkeypatch):
    """thinking shortcut must not be passed raw to the model constructor (excluded from model_dump)."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    cfg = _make_app_config(
        [
            _make_model(
                "no-leak",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                supports_reasoning_effort=False,
                thinking=thinking_settings,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="no-leak", thinking_enabled=False)

    # The disable path should have set thinking to disabled (not the raw enabled shortcut)
    assert captured.get("thinking") == {"type": "disabled"}


def test_dynamic_model_token_forwards_thread_id_to_resolver(monkeypatch):
    cfg = _make_app_config([_make_model("dynamic-model")])
    _patch_factory(monkeypatch, cfg)

    captured: dict[str, object] = {}

    def _fake_resolve_chat_model_spec(name=None, dynamic_model_token=None, thread_id=None):
        captured["name"] = name
        captured["dynamic_model_token"] = dynamic_model_token
        captured["thread_id"] = thread_id
        return _build_spec_from_app_config(cfg, "dynamic-model")

    monkeypatch.setattr(factory_module, "resolve_chat_model_spec", _fake_resolve_chat_model_spec)

    factory_module.create_chat_model(
        name="dynamic-model",
        dynamic_model_token="token-123",
        thread_id="thread-abc",
    )

    assert captured["dynamic_model_token"] == "token-123"
    assert captured["thread_id"] == "thread-abc"


def test_resolved_spec_payload_bypasses_runtime_resolution(monkeypatch):
    cfg = _make_app_config([_make_model("fallback-model")])
    _patch_factory(monkeypatch, cfg)

    payload = dump_resolved_chat_model_spec(
        ResolvedChatModelSpec(
            name="serialized-model",
            display_name="Serialized Model",
            description=None,
            use="langchain_openai:ChatOpenAI",
            config={"model": "resolved-from-payload", "temperature": 0.3},
            supports_vision=True,
            supports_thinking=False,
            supports_reasoning_effort=False,
        )
    )

    monkeypatch.setattr(
        factory_module,
        "resolve_chat_model_spec",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("resolver should not be called")),
    )

    FakeChatModel.captured_kwargs = {}
    factory_module.create_chat_model(
        name="ignored-model-name",
        dynamic_model_token="ignored-token",
        thread_id="ignored-thread",
        resolved_spec_payload=payload,
    )

    assert FakeChatModel.captured_kwargs["model"] == "resolved-from-payload"
    assert FakeChatModel.captured_kwargs["temperature"] == 0.3
