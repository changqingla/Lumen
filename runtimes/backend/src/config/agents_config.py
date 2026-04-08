"""自定义 Agent 的配置与加载器。"""

import logging
import re
from typing import Any

import yaml
from pydantic import BaseModel

from src.config.paths import get_paths

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class AgentConfig(BaseModel):
    """自定义 Agent 配置。"""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None


def load_agent_config(name: str | None) -> AgentConfig | None:
    """
    加载指定 Agent 的配置。

    参数：
        name: Agent 名称。

    返回：
        AgentConfig 实例。

    异常：
        FileNotFoundError: 当 Agent 目录或 config.yaml 不存在时抛出。
        ValueError: 当 config.yaml 解析失败时抛出。
    """

    if name is None:
        return None

    if not AGENT_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    agent_dir = get_paths().agent_dir(name)
    config_file = agent_dir / "config.yaml"

    if not agent_dir.exists():
        raise FileNotFoundError(f"Agent directory not found: {agent_dir}")

    if not config_file.exists():
        raise FileNotFoundError(f"Agent config not found: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse agent config {config_file}: {e}") from e

    # 若配置文件未提供 name，则使用目录名
    if "name" not in data:
        data["name"] = name

    # 传给 Pydantic 前先剔除未知字段（例如旧版 prompt_file）
    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}

    return AgentConfig(**data)


def load_agent_soul(agent_name: str | None) -> str | None:
    """
    加载 Agent 的 SOUL.md 内容。

    SOUL.md 定义 Agent 的人格、价值观和行为边界，
    会作为附加上下文注入主 Agent 系统提示词。

    参数：
        agent_name: Agent 名称；None 表示默认 Agent。

    返回：
        SOUL.md 文本内容；若文件不存在则返回 None。
    """
    agent_dir = get_paths().agent_dir(agent_name) if agent_name else get_paths().base_dir
    soul_path = agent_dir / SOUL_FILENAME
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def list_custom_agents() -> list[AgentConfig]:
    """
    列出所有可用的自定义 Agent。

    返回：
        每个有效 Agent 目录对应的 AgentConfig 列表。
    """
    agents_dir = get_paths().agents_dir

    if not agents_dir.exists():
        return []

    agents: list[AgentConfig] = []

    for entry in sorted(agents_dir.iterdir()):
        if not entry.is_dir():
            continue

        config_file = entry / "config.yaml"
        if not config_file.exists():
            logger.debug(f"Skipping {entry.name}: no config.yaml")
            continue

        try:
            agent_cfg = load_agent_config(entry.name)
            agents.append(agent_cfg)
        except Exception as e:
            logger.warning(f"Skipping agent '{entry.name}': {e}")

    return agents
