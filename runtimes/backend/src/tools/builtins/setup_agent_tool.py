import logging

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

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

    agent_name: str | None = runtime.context.get("agent_name")

    try:
        paths = get_paths()
        agent_dir = paths.agent_dir(agent_name) if agent_name else paths.base_dir
        agent_dir.mkdir(parents=True, exist_ok=True)

        if agent_name:
            # 传入 agent_name 时，在 agents/ 目录创建自定义 Agent
            config_data: dict = {"name": agent_name}
            if description:
                config_data["description"] = description

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(soul, encoding="utf-8")

        logger.info(f"[agent_creator] Created agent '{agent_name}' at {agent_dir}")
        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=f"Agent '{agent_name}' created successfully!", tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as e:
        import shutil

        if agent_name and agent_dir.exists():
            # 仅在“目录已创建但初始化失败”时清理该自定义 Agent 目录
            shutil.rmtree(agent_dir)
        logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})
