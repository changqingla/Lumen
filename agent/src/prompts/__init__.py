"""提示词模块 - 从 Markdown 文件加载"""
from .prompt_loader import load_prompt

# ReAct Agent 提示词
REACT_AGENT_PROMPT = load_prompt("react_agent")

__all__ = [
    "REACT_AGENT_PROMPT",
]
