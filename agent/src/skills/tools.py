"""Skill tools — load_skill and read_skill_resource for the ReAct agent.

These tools implement progressive disclosure:
  1. Skill metadata (name + description) is always in the system prompt
  2. load_skill reads the full SKILL.md body into context
  3. read_skill_resource reads additional bundled files on demand
"""
import json
from typing import Optional

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from .loader import get_skill_loader, stage_skill_resources
from ..utils.logger import get_logger

logger = get_logger(__name__)


class LoadSkillTool(BaseTool):
    """Load a skill's full instructions into context."""

    name: str = "load_skill"
    description: str = """加载一个 skill 的完整指令到上下文中。当你判断某个 skill 与当前任务相关时使用。

输入: skill 名称（字符串）
输出: skill 的完整指令内容，以及可用的资源文件列表"""

    _state_ref: Optional[dict] = None
    workspace_path: str = ""

    class Config:
        arbitrary_types_allowed = True

    def set_state_ref(self, state_ref: dict) -> None:
        """Set a reference to the AgentState dict so runtime info can be written back."""
        self._state_ref = state_ref

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        skill_name = query.strip().strip('"').strip("'")
        loader = get_skill_loader()

        if not skill_name:
            # List all available skills
            skills = loader.get_all_skills()
            if not skills:
                return "当前没有安装任何 skill。"
            lines = ["可用的 skills:"]
            for s in skills:
                lines.append(f"- {s.name}: {s.description[:100]}")
            return "\n".join(lines)

        skill = loader.get_skill(skill_name)
        if not skill:
            available = [s.name for s in loader.get_all_skills()]
            return f"未找到 skill '{skill_name}'。可用的 skills: {available}"

        # Write runtime info back to state if state_ref is available
        if self._state_ref is not None:
            if skill.runtime:
                rt = skill.runtime
                self._state_ref["active_skill_runtime"] = {
                    "image": rt.image,
                    "network": rt.network,
                    "memory": rt.memory,
                    "timeout": rt.timeout,
                }
            else:
                self._state_ref["active_skill_runtime"] = None

        # Use expanded body (auto-inlines referenced .md sub-documents)
        body = skill.get_expanded_body()
        result = f"# Skill: {skill.name}\n\n{body}"

        # Stage resource files into workspace (scripts, templates, etc.)
        if self.workspace_path:
            try:
                staged = stage_skill_resources(skill, self.workspace_path)
                if staged:
                    result += f"\n\n---\n📂 已将 {staged} 个资源文件复制到工作目录，可直接通过相对路径访问。"
            except Exception as e:
                logger.warning(f"Failed to stage skill resources: {e}")

        # List remaining non-.md resource files that weren't inlined
        non_md_resources = [rf for rf in skill.resource_files if not rf.endswith(".md")]
        if non_md_resources:
            result += "\n\n---\n## 可用资源文件\n"
            result += "使用 read_skill_resource 工具读取以下文件:\n"
            for rf in non_md_resources:
                result += f"- {rf}\n"
        elif skill.resource_files:
            # All resources were .md files that got inlined — no need to list them
            pass

        logger.info(f"📖 Skill loaded: {skill_name} ({len(body)} chars)")
        return result

    async def _arun(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        return self._run(query, run_manager)


class ReadSkillResourceTool(BaseTool):
    """Read a resource file bundled with a skill."""

    name: str = "read_skill_resource"
    description: str = """读取 skill 目录中的附属资源文件。

输入格式（JSON）:
  {"skill": "skill名称", "file": "资源文件相对路径"}

输出: 文件内容"""

    class Config:
        arbitrary_types_allowed = True

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        # Parse input
        try:
            params = json.loads(query) if query.strip().startswith("{") else None
        except json.JSONDecodeError:
            params = None

        if not params or "skill" not in params or "file" not in params:
            return '[ERROR] 输入格式错误。请使用 JSON: {"skill": "skill名称", "file": "文件路径"}'

        skill_name = params["skill"].strip()
        file_path = params["file"].strip()

        loader = get_skill_loader()
        skill = loader.get_skill(skill_name)

        if not skill:
            available = [s.name for s in loader.get_all_skills()]
            return f"未找到 skill '{skill_name}'。可用: {available}"

        if file_path not in skill.resource_files:
            return (
                f"资源文件 '{file_path}' 不存在于 skill '{skill_name}' 中。\n"
                f"可用资源文件: {skill.resource_files}"
            )

        content = loader.read_resource(skill_name, file_path)
        if content is None:
            return f"[ERROR] 无法读取文件: {file_path}"

        logger.info(
            f"📄 Skill resource loaded: {skill_name}/{file_path} "
            f"({len(content)} chars)"
        )
        return f"# {skill_name} / {file_path}\n\n{content}"

    async def _arun(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        return self._run(query, run_manager)
