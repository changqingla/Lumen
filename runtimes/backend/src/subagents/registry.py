"""用于管理可用子代理的注册表。"""

import logging
from dataclasses import replace

from src.subagents.builtins import BUILTIN_SUBAGENTS
from src.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


def get_subagent_config(name: str) -> SubagentConfig | None:
    """按名称获取子代理配置。

    参数：
        name: 子代理名称。

    返回：
        找到时返回 `SubagentConfig`（已应用 `config.yaml` 覆盖项），
        否则返回 None。
    """
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None:
        return None

    # 应用来自 config.yaml 的超时覆盖（延迟导入以避免循环依赖）
    from src.config.subagents_config import get_subagents_app_config

    app_config = get_subagents_app_config()
    effective_timeout = app_config.get_timeout_for(name)
    if effective_timeout != config.timeout_seconds:
        logger.debug(f"Subagent '{name}': timeout overridden by config.yaml ({config.timeout_seconds}s -> {effective_timeout}s)")
        config = replace(config, timeout_seconds=effective_timeout)

    return config


def list_subagents() -> list[SubagentConfig]:
    """返回所有已注册子代理配置列表。"""
    return [get_subagent_config(name) for name in BUILTIN_SUBAGENTS]
