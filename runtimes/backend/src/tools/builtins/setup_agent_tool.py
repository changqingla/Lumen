import logging
from pathlib import Path

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from src.agents.memory.scope import normalize_agent_name
from src.config.paths import get_paths

logger = logging.getLogger(__name__)


@tool
def setup_agent(
    soul: str,
    description: str,
    runtime: ToolRuntime,
) -> Command:
    """
    参数：
        soul: 定义 Agent 个性与行为的完整 SOUL.md 内容。
        description: Agent 功能的一行描述。

    """

    raw_agent_name = runtime.context.get("agent_name")
    agent_dir: Path | None = None
    created_agent_dir = False

    try:
        agent_name = normalize_agent_name(raw_agent_name)
        paths = get_paths()
        agent_dir = paths.agent_dir(agent_name) if agent_name else paths.base_dir

        if agent_name:
            agent_dir.mkdir(parents=True, exist_ok=False)
            created_agent_dir = True
            # 传入 agent_name 时，在 agents/ 目录创建自定义 Agent
            config_data: dict = {"name": agent_name}
            if description:
                config_data["description"] = description

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
        else:
            agent_dir.mkdir(parents=True, exist_ok=True)

        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(soul, encoding="utf-8")

        logger.info("[agent_creator] Created agent definition")
        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=f"Agent '{agent_name}' created successfully!", tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as exc:
        import shutil

        if created_agent_dir and agent_dir is not None and agent_dir.exists():
            # 仅在“目录已创建但初始化失败”时清理该自定义 Agent 目录
            shutil.rmtree(agent_dir, ignore_errors=True)
        logger.error(
            "[agent_creator] Failed to create agent (%s)",
            type(exc).__name__,
        )
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Error: agent creation failed",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )
