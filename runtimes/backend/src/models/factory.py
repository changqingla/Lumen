import logging
from collections.abc import Mapping

from langchain.chat_models import BaseChatModel

from src.config import get_app_config, get_tracing_config, is_tracing_enabled
from src.models.resolver import ResolvedChatModelSpec, resolve_chat_model_spec
from src.reflection import resolve_class
from src.utils.outbound_endpoint_policy import OutboundEndpointError, OutboundEndpointPolicy

logger = logging.getLogger(__name__)

_OPENAI_COMPATIBLE_MODEL_USE = "langchain_openai:ChatOpenAI"
_PATCHED_OPENAI_COMPATIBLE_MODEL_USE = "src.models.patched_openai:PatchedChatOpenAI"
_OPENAI_COMPATIBLE_MODEL_USES = frozenset(
    {
        _OPENAI_COMPATIBLE_MODEL_USE,
        _PATCHED_OPENAI_COMPATIBLE_MODEL_USE,
    }
)
_OPENAI_COMPATIBLE_DEFAULT_USER_AGENT = "Lumen/1.0"


def _get_supported_model_config_keys(model_class: type[BaseChatModel]) -> set[str] | None:
    """返回模型构造器明确支持的配置键集合。

    仅接受模型类声明过的字段名及其别名，避免把陈旧配置透传到底层 SDK。
    """
    model_fields = getattr(model_class, "model_fields", None)
    if not isinstance(model_fields, dict):
        return None

    supported_keys: set[str] = set()
    for field_name, field_info in model_fields.items():
        supported_keys.add(field_name)
        alias = getattr(field_info, "alias", None)
        if isinstance(alias, str) and alias:
            supported_keys.add(alias)
    return supported_keys


def _sanitize_max_tokens(settings: dict, *, model_name: str, model_use: str, source: str) -> dict:
    """清理并保护 max_tokens，避免兼容 OpenAI 的网关因超限直接报错。"""
    sanitized = dict(settings)
    max_tokens = sanitized.get("max_tokens")
    if not isinstance(max_tokens, int):
        return sanitized

    if max_tokens < 1:
        logger.warning(
            "Ignoring invalid max_tokens=%s for model '%s' from %s; expected a positive integer.",
            max_tokens,
            model_name,
            source,
        )
        sanitized.pop("max_tokens", None)
        return sanitized

    if model_use in _OPENAI_COMPATIBLE_MODEL_USES and max_tokens > 65536:
        logger.warning(
            "Clamping max_tokens for model '%s' from %s to 65536 (configured=%s) to stay within OpenAI-compatible provider limits.",
            model_name,
            source,
            max_tokens,
        )
        sanitized["max_tokens"] = 65536

    return sanitized


def _with_openai_compatible_default_headers(settings: dict, *, model_use: str) -> dict:
    """Set a stable User-Agent for OpenAI-compatible gateways unless explicitly configured."""
    if model_use not in _OPENAI_COMPATIBLE_MODEL_USES:
        return settings

    updated = dict(settings)
    existing_headers = updated.get("default_headers")
    if existing_headers is None:
        updated["default_headers"] = {"User-Agent": _OPENAI_COMPATIBLE_DEFAULT_USER_AGENT}
        return updated

    if not isinstance(existing_headers, Mapping):
        return updated

    if any(str(header_name).lower() == "user-agent" for header_name in existing_headers):
        return updated

    updated["default_headers"] = {**existing_headers, "User-Agent": _OPENAI_COMPATIBLE_DEFAULT_USER_AGENT}
    return updated


def _with_dynamic_endpoint_policy(
    settings: dict,
    *,
    spec: ResolvedChatModelSpec,
) -> dict:
    """Validate dynamic endpoints and secure OpenAI-compatible HTTP clients."""

    if not spec.enforce_outbound_endpoint_policy:
        return settings

    updated = dict(settings)
    base_url_keys = [key for key in ("base_url", "openai_api_base") if key in updated]
    if not base_url_keys:
        raise OutboundEndpointError()
    raw_base_urls = [updated[key] for key in base_url_keys]
    if any(not isinstance(value, str) or not value.strip() for value in raw_base_urls):
        raise OutboundEndpointError()
    normalized_inputs = {str(value).strip() for value in raw_base_urls}
    if len(normalized_inputs) != 1:
        raise OutboundEndpointError()
    raw_base_url = str(raw_base_urls[0])

    policy = OutboundEndpointPolicy.from_environment()
    validated_base_url = policy.validate_url(raw_base_url)
    for base_url_key in base_url_keys:
        updated[base_url_key] = validated_base_url

    # Custom endpoints are OpenAI-compatible today. Injecting both clients makes
    # sync, async, streaming, and SDK retries pass through the same request hook.
    if spec.use in _OPENAI_COMPATIBLE_MODEL_USES:
        http_client, http_async_client = policy.build_http_clients()
        for client_key in ("client", "async_client", "root_client", "root_async_client"):
            updated.pop(client_key, None)
        updated.update(
            {
                "http_client": http_client,
                "http_async_client": http_async_client,
                # Explicit None prevents SDK/environment proxy settings from
                # bypassing the checked destination.
                "openai_proxy": None,
            }
        )

    return updated


def create_chat_model_from_spec(
    spec: ResolvedChatModelSpec,
    *,
    thinking_enabled: bool = False,
    **kwargs,
) -> BaseChatModel:
    """Create a chat model instance from a resolved model spec."""
    model_class = resolve_class(spec.use, BaseChatModel)
    model_settings_from_config = dict(spec.config)
    supported_config_keys = _get_supported_model_config_keys(model_class)
    if supported_config_keys is not None:
        unsupported_keys = sorted(set(model_settings_from_config) - supported_config_keys)
        if unsupported_keys:
            logger.warning(
                "Ignoring unsupported model config keys for '%s' (%s): %s",
                spec.name,
                spec.use,
                ", ".join(unsupported_keys),
            )
            model_settings_from_config = {key: value for key, value in model_settings_from_config.items() if key in supported_config_keys}

    has_thinking_settings = (spec.when_thinking_enabled is not None) or (spec.thinking is not None)
    effective_wte: dict = dict(spec.when_thinking_enabled) if spec.when_thinking_enabled else {}
    if spec.thinking is not None:
        merged_thinking = {**(effective_wte.get("thinking") or {}), **spec.thinking}
        effective_wte = {**effective_wte, "thinking": merged_thinking}

    if thinking_enabled and has_thinking_settings:
        if not spec.supports_thinking:
            raise ValueError(f"Model {spec.name} does not support thinking. Set `supports_thinking` to true in the model registry/config to enable thinking.") from None
        if effective_wte:
            model_settings_from_config.update(effective_wte)
    if thinking_enabled and not has_thinking_settings and not spec.supports_thinking:
        logger.warning("Thinking mode requested but model '%s' does not advertise thinking support.", spec.name)
    if not thinking_enabled and has_thinking_settings:
        if effective_wte.get("extra_body", {}).get("thinking", {}).get("type"):
            kwargs.update({"extra_body": {"thinking": {"type": "disabled"}}})
            kwargs.update({"reasoning_effort": "minimal"})
        elif effective_wte.get("thinking", {}).get("type"):
            kwargs.update({"thinking": {"type": "disabled"}})
    if not spec.supports_reasoning_effort and "reasoning_effort" in kwargs:
        del kwargs["reasoning_effort"]

    model_settings_from_config = _sanitize_max_tokens(
        model_settings_from_config,
        model_name=spec.name,
        model_use=spec.use,
        source="config",
    )
    kwargs = _sanitize_max_tokens(
        kwargs,
        model_name=spec.name,
        model_use=spec.use,
        source="runtime overrides",
    )

    final_model_settings = {**model_settings_from_config, **kwargs}
    final_model_settings = _with_openai_compatible_default_headers(
        final_model_settings,
        model_use=spec.use,
    )
    final_model_settings = _with_dynamic_endpoint_policy(
        final_model_settings,
        spec=spec,
    )
    model_instance = model_class(**final_model_settings)

    if is_tracing_enabled():
        try:
            from langchain_core.tracers.langchain import LangChainTracer

            tracing_config = get_tracing_config()
            tracer = LangChainTracer(
                project_name=tracing_config.project,
            )
            existing_callbacks = model_instance.callbacks or []
            model_instance.callbacks = [*existing_callbacks, tracer]
            logger.debug(f"LangSmith tracing attached to model '{spec.name}' (project='{tracing_config.project}')")
        except Exception as exc:
            logger.warning(
                "Failed to attach LangSmith tracing (%s)",
                type(exc).__name__,
            )
    return model_instance


def create_chat_model(
    name: str | None = None,
    thinking_enabled: bool = False,
    *,
    dynamic_model_token: str | None = None,
    thread_id: str | None = None,
    **kwargs,
) -> BaseChatModel:
    """创建聊天模型实例。

    参数：
        name: 待创建模型名；为 None 时使用配置中的第一个模型。
        dynamic_model_token: 动态模型绑定令牌；提供时优先从业务后端解析模型。

    返回：
        聊天模型实例。
    """
    if dynamic_model_token:
        spec = resolve_chat_model_spec(
            name,
            dynamic_model_token=dynamic_model_token,
            thread_id=thread_id,
        )
    else:
        config = get_app_config()
        if name is None:
            name = config.models[0].name
        spec = resolve_chat_model_spec(name)
    return create_chat_model_from_spec(spec, thinking_enabled=thinking_enabled, **kwargs)
