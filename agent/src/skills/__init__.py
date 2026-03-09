"""Skills module — Anthropic Agent Skills compatible loader.

Supports the open SKILL.md format: a directory containing a SKILL.md file
with YAML frontmatter (name, description) and optional resource files.

Skills are discovered at startup, metadata injected into the system prompt,
and full content loaded on-demand via progressive disclosure.
"""
from .loader import SkillLoader, SkillMetadata, SkillRuntime, get_skill_loader, stage_skill_resources
from .tools import LoadSkillTool, ReadSkillResourceTool

__all__ = [
    "SkillLoader",
    "SkillMetadata",
    "SkillRuntime",
    "get_skill_loader",
    "stage_skill_resources",
    "LoadSkillTool",
    "ReadSkillResourceTool",
]
