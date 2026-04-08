import logging
import os
import threading

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
_config_lock = threading.Lock()


class TracingConfig(BaseModel):
    """用于 LangSmith 的链路追踪配置。"""

    enabled: bool = Field(...)
    api_key: str | None = Field(...)
    project: str = Field(...)
    endpoint: str = Field(...)

    @property
    def is_configured(self) -> bool:
        """检查追踪是否已完整配置（启用且包含 API Key）。"""
        return self.enabled and bool(self.api_key)


_tracing_config: TracingConfig | None = None


_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _env_flag_preferred(*names: str) -> bool:
    """
    按优先级读取布尔环境变量。

    可识别的真值（不区分大小写）：``1``、``true``、``yes``、``on``。
    其余非空值均视为假值。若给定变量都未设置，则返回 ``False``。
    """
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip().lower() in _TRUTHY_VALUES
    return False


def _first_env_value(*names: str) -> str | None:
    """从候选变量名中返回第一个非空环境变量值。"""
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def get_tracing_config() -> TracingConfig:
    """
    获取追踪配置。

    ``LANGSMITH_*`` 变量优先于旧版 ``LANGCHAIN_*`` 变量。
    对于布尔标志（``enabled``），会在优先级列表中选取第一个存在且非空的变量作为唯一依据，
    解析后直接返回，不再继续检查后续候选项。真值包含 ``1``、``true``、``yes``、``on``
    （不区分大小写）；其余非空值视为假值。

    优先级顺序：
        enabled  : LANGSMITH_TRACING > LANGCHAIN_TRACING_V2 > LANGCHAIN_TRACING
        api_key  : LANGSMITH_API_KEY  > LANGCHAIN_API_KEY
        project  : LANGSMITH_PROJECT  > LANGCHAIN_PROJECT   (default: "lumen")
        endpoint : LANGSMITH_ENDPOINT > LANGCHAIN_ENDPOINT  (default: https://api.smith.langchain.com)

    返回：
        当前设置对应的 TracingConfig。
    """
    global _tracing_config
    if _tracing_config is not None:
        return _tracing_config
    with _config_lock:
        if _tracing_config is not None:  # 加锁后再次检查
            return _tracing_config
        _tracing_config = TracingConfig(
            # 同时兼容旧版 LANGCHAIN_* 与新版 LANGSMITH_* 变量。
            enabled=_env_flag_preferred("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"),
            api_key=_first_env_value("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"),
            project=_first_env_value("LANGSMITH_PROJECT", "LANGCHAIN_PROJECT") or "lumen",
            endpoint=_first_env_value("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT") or "https://api.smith.langchain.com",
        )
        return _tracing_config


def is_tracing_enabled() -> bool:
    """
    返回：
        若追踪已启用且存在 API Key，则返回 True。
    """
    return get_tracing_config().is_configured
