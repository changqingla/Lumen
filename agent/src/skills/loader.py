"""Skill loader — discovers and parses SKILL.md files.

Compatible with the Anthropic Agent Skills open standard:
  https://github.com/anthropics/skills

Each skill is a directory containing a SKILL.md with YAML frontmatter:
  ---
  name: my-skill
  description: What this skill does and when to trigger it.
  ---
  # Full instructions ...
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from ..utils.logger import get_logger

logger = get_logger(__name__)

# Default skills directory (relative to agent/)
DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


@dataclass
class SkillRuntime:
    """Runtime environment declaration from SKILL.md frontmatter."""
    image: str
    network: str = "none"
    memory: str = "512m"
    timeout: int = 120


@dataclass
class SkillMetadata:
    """Parsed metadata from a SKILL.md file."""
    name: str
    description: str
    skill_dir: Path  # absolute path to the skill directory
    skill_file: Path  # absolute path to SKILL.md
    resource_files: List[str] = field(default_factory=list)  # relative filenames
    runtime: Optional[SkillRuntime] = None

    @property
    def body(self) -> str:
        """Read the full SKILL.md body (everything after frontmatter)."""
        return _read_skill_body(self.skill_file)

    def get_expanded_body(self) -> str:
        """Read the SKILL.md body with referenced .md sub-documents inlined.

        Detects Markdown links like [text](file.md) where file.md exists as a
        resource file, and appends the full content of each referenced document
        at the end of the body. This follows the Anthropic Agent Skills pattern
        where SKILL.md references sub-documents (e.g. editing.md, pptxgenjs.md).
        """
        body = self.body
        expanded_docs = _find_and_expand_md_references(body, self.skill_dir, self.resource_files)
        if not expanded_docs:
            return body

        parts = [body]
        for ref_path, content in expanded_docs:
            parts.append(f"\n\n---\n## 📄 {ref_path}\n\n{content}")
        return "\n".join(parts)


# ── Frontmatter parsing ─────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n?(.*)",
    re.DOTALL,
)

# Matches Markdown links like [text](file.md) — captures the relative path
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+\.md)\)")


def _find_and_expand_md_references(
    body: str, skill_dir: Path, resource_files: List[str]
) -> List[tuple]:
    """Find Markdown links to .md files that exist as resources and read their content.

    Returns a list of (relative_path, content) tuples for each referenced doc.
    Deduplicates and preserves order of first occurrence.
    """
    seen = set()
    results = []
    for _link_text, ref_path in _MD_LINK_RE.findall(body):
        # Normalize the path (handle things like ./editing.md)
        normalized = str(Path(ref_path))
        if normalized in seen:
            continue
        if normalized not in resource_files:
            continue
        target = (skill_dir / normalized).resolve()
        # Security: ensure within skill_dir
        if not str(target).startswith(str(skill_dir.resolve())):
            continue
        if not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8")
            # If the sub-doc itself has frontmatter, strip it
            _, sub_body = _parse_frontmatter(content)
            results.append((normalized, sub_body.strip()))
            seen.add(normalized)
        except Exception as e:
            logger.warning(f"Failed to expand sub-document {ref_path}: {e}")
    return results


def _parse_frontmatter(text: str) -> tuple:
    """Return (yaml_dict, body) from a SKILL.md file's text."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_str, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_str)
    except yaml.YAMLError as e:
        logger.warning(f"Failed to parse SKILL.md frontmatter: {e}")
        return {}, text
    return (fm if isinstance(fm, dict) else {}), body


def _read_skill_body(skill_file: Path) -> str:
    """Read the full body (after frontmatter) of a SKILL.md."""
    text = skill_file.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)
    return body.strip()


# ── Skill discovery ──────────────────────────────────────────────────────

def _discover_resource_files(skill_dir: Path) -> List[str]:
    """List non-SKILL.md files in a skill directory (recursively)."""
    # Files to ignore
    _IGNORE = {".gitkeep", ".DS_Store", "Thumbs.db", "__pycache__"}
    resources = []
    for root, _dirs, files in os.walk(skill_dir):
        # Skip hidden directories and __pycache__
        _dirs[:] = [d for d in _dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            if f in _IGNORE or f.startswith("."):
                continue
            full = Path(root) / f
            rel = full.relative_to(skill_dir)
            if rel.name.upper() != "SKILL.MD":
                resources.append(str(rel))
    return sorted(resources)


def _load_single_skill(skill_dir: Path) -> Optional[SkillMetadata]:
    """Load a single skill from its directory. Returns None on failure."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return None

    text = skill_file.read_text(encoding="utf-8")
    fm, _ = _parse_frontmatter(text)

    name = fm.get("name")
    description = fm.get("description")

    if not name or not description:
        logger.warning(
            f"Skipping skill at {skill_dir}: "
            "SKILL.md must have 'name' and 'description' in frontmatter"
        )
        return None

    resources = _discover_resource_files(skill_dir)

    # Parse optional runtime field
    runtime: Optional[SkillRuntime] = None
    runtime_raw = fm.get("runtime")
    if isinstance(runtime_raw, dict) and runtime_raw.get("image"):
        runtime = SkillRuntime(
            image=runtime_raw["image"],
            network=runtime_raw.get("network", "none"),
            memory=runtime_raw.get("memory", "512m"),
            timeout=runtime_raw.get("timeout", 120),
        )

    logger.info(f"📦 Loaded skill: {name} ({len(resources)} resource files)")
    return SkillMetadata(
        name=name,
        description=description,
        skill_dir=skill_dir,
        skill_file=skill_file,
        resource_files=resources,
        runtime=runtime,
    )


class SkillLoader:
    """Discovers and manages skills from a directory."""

    def __init__(self, skills_dir: Optional[str] = None):
        self.skills_dir = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
        self.skills: Dict[str, SkillMetadata] = {}

    def discover(self) -> int:
        """Scan skills directory and load all valid skills.

        Returns:
            Number of skills loaded.
        """
        self.skills.clear()

        if not self.skills_dir.is_dir():
            logger.info(f"Skills directory not found: {self.skills_dir} — no skills loaded")
            return 0

        for entry in sorted(self.skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill = _load_single_skill(entry)
            if skill:
                if skill.name in self.skills:
                    logger.warning(f"Duplicate skill name '{skill.name}', overwriting")
                self.skills[skill.name] = skill

        logger.info(f"✅ Discovered {len(self.skills)} skill(s)")
        return len(self.skills)

    def get_skill(self, name: str) -> Optional[SkillMetadata]:
        return self.skills.get(name)

    def get_all_skills(self) -> List[SkillMetadata]:
        return list(self.skills.values())

    def get_metadata_summary(self) -> str:
        """Build a summary string of all skill names + descriptions.

        This is injected into the system prompt so the LLM knows
        which skills are available and when to trigger them.
        """
        if not self.skills:
            return ""

        lines = ["# ⚠️ Available Skills — 必须优先检查\n"]
        lines.append(
            "**重要**: 如果用户的请求匹配了以下任何 skill 的描述，你 **必须** 先调用 "
            "`load_skill` 加载该 skill 的完整指令，然后按指令执行。"
            "**绝对不要** 跳过 skill 直接用纯文本回答。\n"
        )
        for i, skill in enumerate(self.skills.values(), 1):
            resources_hint = ""
            if skill.resource_files:
                resources_hint = f" (has {len(skill.resource_files)} resource files)"
            lines.append(
                f"{i}. **{skill.name}**{resources_hint}\n"
                f"   {skill.description}\n"
            )
        lines.append(
            "To use a skill, call `load_skill` with the skill name. "
            "To read a skill's bundled resource file, call `read_skill_resource`."
        )
        return "\n".join(lines)

    def read_resource(self, skill_name: str, resource_path: str) -> Optional[str]:
        """Read a resource file from a skill directory.

        Args:
            skill_name: The skill's name.
            resource_path: Relative path within the skill directory.

        Returns:
            File content as string, or None if not found.
        """
        skill = self.skills.get(skill_name)
        if not skill:
            return None

        # Security: prevent path traversal
        target = (skill.skill_dir / resource_path).resolve()
        if not str(target).startswith(str(skill.skill_dir.resolve())):
            logger.warning(f"Path traversal attempt blocked: {resource_path}")
            return None

        if not target.is_file():
            return None

        try:
            return target.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read skill resource {resource_path}: {e}")
            return None


# ── Global singleton ─────────────────────────────────────────────────────

_global_loader: Optional[SkillLoader] = None


def get_skill_loader(skills_dir: Optional[str] = None) -> SkillLoader:
    """Get or create the global SkillLoader instance."""
    global _global_loader
    if _global_loader is None:
        _global_loader = SkillLoader(skills_dir)
        _global_loader.discover()
    return _global_loader


def reset_skill_loader() -> None:
    """Reset the global loader (for testing)."""
    global _global_loader
    _global_loader = None


def stage_skill_resources(skill: SkillMetadata, workspace_path: str) -> int:
    """Copy a skill's resource files into the workspace directory.

    Replicates the skill's directory structure (excluding SKILL.md) directly
    into the workspace root. For example, if the skill has:
        scripts/thumbnail.py
        scripts/office/soffice.py
    They become:
        {workspace}/scripts/thumbnail.py
        {workspace}/scripts/office/soffice.py

    This enables commands like ``python scripts/thumbnail.py`` to work
    naturally when the workspace is the cwd — matching the Anthropic Agent
    Skills convention where scripts are referenced relative to the skill root.

    Args:
        skill: The skill whose resources should be staged.
        workspace_path: Absolute path to the workspace directory.

    Returns:
        Number of files copied.
    """
    import shutil

    workspace = Path(workspace_path)
    copied = 0
    for rel in skill.resource_files:
        src = skill.skill_dir / rel
        dst = workspace / rel
        if not src.is_file():
            continue
        # Security: ensure src is within skill_dir
        if not str(src.resolve()).startswith(str(skill.skill_dir.resolve())):
            logger.warning(f"Skipping resource outside skill dir: {rel}")
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            copied += 1
        except Exception as e:
            logger.warning(f"Failed to stage resource {rel}: {e}")
    if copied:
        logger.info(f"📂 Staged {copied} resource files from skill '{skill.name}' into workspace")
    return copied
