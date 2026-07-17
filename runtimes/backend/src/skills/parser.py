import logging
import re
from pathlib import Path

from .types import Skill

logger = logging.getLogger(__name__)


def parse_skill_file(skill_file: Path, category: str, relative_path: Path | None = None) -> Skill | None:
    """解析 `SKILL.md` 并提取元数据。

    参数：
        skill_file: `SKILL.md` 文件路径。
        category: 技能类别（`public` 或 `custom`）。

    返回：
        解析成功返回 Skill 对象，否则返回 None。
    """
    if not skill_file.exists() or skill_file.name != "SKILL.md":
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")

        # 提取 YAML 头部元数据
        # 形如：---\nkey: value\n---
        front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)

        if not front_matter_match:
            return None

        front_matter = front_matter_match.group(1)

        # 解析 YAML 头部元数据（简单的键值对解析）
        metadata = {}
        for line in front_matter.split("\n"):
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()

        # 提取必填字段
        name = metadata.get("name")
        description = metadata.get("description")

        if not name or not description:
            return None

        license_text = metadata.get("license")

        return Skill(
            name=name,
            description=description,
            license=license_text,
            skill_dir=skill_file.parent,
            skill_file=skill_file,
            relative_path=relative_path or Path(skill_file.parent.name),
            category=category,
            enabled=True,  # 默认启用，实际状态以配置文件为准
        )

    except Exception as exc:
        logger.warning(
            "Skipping an invalid skill definition (error_type=%s)",
            type(exc).__name__,
        )
        return None
