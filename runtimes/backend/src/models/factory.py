import json
import logging
import types
from collections.abc import Mapping

from langchain.chat_models import BaseChatModel

from src.config import get_app_config, get_tracing_config, is_tracing_enabled
from src.models.resolver import (
    ResolvedChatModelSpec,
    load_resolved_chat_model_spec,
    resolve_chat_model_spec,
)
from src.reflection import resolve_class

logger = logging.getLogger(__name__)

_REDACTED = "***REDACTED***"
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "openai_api_key",
        "anthropic_api_key",
        "google_api_key",
    }
)
_SENSITIVE_HEADER_NAMES = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key"})


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


def _serialize_value_for_logging(value):
    """将请求体递归转换为可 JSON 序列化的结构，并对敏感字段做脱敏。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return {"type": "bytes", "size": len(value)}

    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _serialize_value_for_logging(value.model_dump(mode="json"))
        except TypeError:
            return _serialize_value_for_logging(value.model_dump())
        except Exception:
            return repr(value)

    if isinstance(value, Mapping):
        serialized = {}
        for key, item in value.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower in _SENSITIVE_FIELD_NAMES or key_lower.endswith("_api_key"):
                serialized[key_str] = _REDACTED
                continue
            if key_lower == "headers" and isinstance(item, Mapping):
                serialized[key_str] = {
                    str(header_name): (
                        _REDACTED if str(header_name).lower() in _SENSITIVE_HEADER_NAMES else _serialize_value_for_logging(header_value)
                    )
                    for header_name, header_value in item.items()
                }
                continue
            serialized[key_str] = _serialize_value_for_logging(item)
        return serialized

    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialize_value_for_logging(item) for item in value]

    return repr(value)


def _attach_request_payload_logger(model_instance: BaseChatModel, *, config_name: str) -> None:
    """为支持 `_get_request_payload()` 的模型实例打印最终请求体。"""
    original_get_request_payload = getattr(model_instance, "_get_request_payload", None)
    if not callable(original_get_request_payload):
        return

    if getattr(original_get_request_payload, "_lumen_request_payload_logging", False):
        return

    def _wrapped_get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = original_get_request_payload(input_, stop=stop, **kwargs)
        if logger.isEnabledFor(logging.INFO):
            try:
                logger.info(
                    "LLM request payload (config=%s, provider_model=%s): %s",
                    config_name,
                    getattr(self, "model_name", None) or getattr(self, "model", None) or config_name,
                    json.dumps(_serialize_value_for_logging(payload), ensure_ascii=False, default=repr),
                )
            except Exception:
                logger.exception(
                    "Failed to serialize LLM request payload for config '%s' and provider model '%s'",
                    config_name,
                    getattr(self, "model_name", None) or getattr(self, "model", None) or config_name,
                )
        return payload

    _wrapped_get_request_payload._lumen_request_payload_logging = True
    model_instance._get_request_payload = types.MethodType(_wrapped_get_request_payload, model_instance)


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

    if model_use == "langchain_openai:ChatOpenAI" and max_tokens > 65536:
        logger.warning(
            "Clamping max_tokens for model '%s' from %s to 65536 (configured=%s) to stay within OpenAI-compatible provider limits.",
            model_name,
            source,
            max_tokens,
        )
        sanitized["max_tokens"] = 65536

    return sanitized


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
            model_settings_from_config = {
                key: value for key, value in model_settings_from_config.items() if key in supported_config_keys
            }

    has_thinking_settings = (spec.when_thinking_enabled is not None) or (spec.thinking is not None)
    effective_wte: dict = dict(spec.when_thinking_enabled) if spec.when_thinking_enabled else {}
    if spec.thinking is not None:
        merged_thinking = {**(effective_wte.get("thinking") or {}), **spec.thinking}
        effective_wte = {**effective_wte, "thinking": merged_thinking}

    if thinking_enabled and has_thinking_settings:
        if not spec.supports_thinking:
            raise ValueError(
                f"Model {spec.name} does not support thinking. "
                "Set `supports_thinking` to true in the model registry/config to enable thinking."
            ) from None
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
    model_instance = model_class(**final_model_settings)
    _attach_request_payload_logger(model_instance, config_name=spec.name)

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
        except Exception as e:
            logger.warning(f"Failed to attach LangSmith tracing to model '{spec.name}': {e}")
    return model_instance


def create_chat_model(
    name: str | None = None,
    thinking_enabled: bool = False,
    *,
    dynamic_model_token: str | None = None,
    thread_id: str | None = None,
    resolved_spec_payload: dict | None = None,
    **kwargs,
) -> BaseChatModel:
    """创建聊天模型实例。

    参数：
        name: 待创建模型名；为 None 时使用配置中的第一个模型。
        dynamic_model_token: 动态模型绑定令牌；提供时优先从业务后端解析模型。

    返回：
        聊天模型实例。
    """
    resolved_spec = load_resolved_chat_model_spec(resolved_spec_payload)
    if resolved_spec is not None:
        spec = resolved_spec
    elif dynamic_model_token:
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
