"""从 config.yaml 加载的子代理系统配置。"""

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SubagentOverrideConfig(BaseModel):
    """按子代理名称覆盖的配置项。"""

    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Timeout in seconds for this subagent (None = use global default)",
    )


class SubagentsAppConfig(BaseModel):
    """子代理系统总配置。"""

    timeout_seconds: int = Field(
        default=900,
        ge=1,
        description="Default timeout in seconds for all subagents (default: 900 = 15 minutes)",
    )
    agents: dict[str, SubagentOverrideConfig] = Field(
        default_factory=dict,
        description="Per-agent configuration overrides keyed by agent name",
    )

    def get_timeout_for(self, agent_name: str) -> int:
        """获取指定子代理的超时时间（秒）。

        参数：
            agent_name: 子代理名称。

        返回：
            若存在子代理级覆盖则返回覆盖值，否则返回全局默认值。
        """
        override = self.agents.get(agent_name)
        if override is not None and override.timeout_seconds is not None:
            return override.timeout_seconds
        return self.timeout_seconds


_subagents_config: SubagentsAppConfig = SubagentsAppConfig()


def get_subagents_app_config() -> SubagentsAppConfig:
    """获取当前子代理配置。"""
    return _subagents_config


def load_subagents_config_from_dict(config_dict: dict) -> None:
    """从字典加载子代理配置。"""
    global _subagents_config
    _subagents_config = SubagentsAppConfig(**config_dict)

    overrides_summary = {name: f"{override.timeout_seconds}s" for name, override in _subagents_config.agents.items() if override.timeout_seconds is not None}
    if overrides_summary:
        logger.info(f"Subagents config loaded: default timeout={_subagents_config.timeout_seconds}s, per-agent overrides={overrides_summary}")
    else:
        logger.info(f"Subagents config loaded: default timeout={_subagents_config.timeout_seconds}s, no per-agent overrides")
