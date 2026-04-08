from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    """表示一个技能对象，包含元数据与文件路径。"""

    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path  # 从分类根目录到技能目录的相对路径
    category: str  # 技能分类：`public` 或 `custom`
    enabled: bool = False  # 该技能是否启用

    @property
    def skill_path(self) -> str:
        """返回从分类根目录（skills/{category}）到技能目录的相对路径。"""
        path = self.relative_path.as_posix()
        return "" if path == "." else path

    def get_container_path(self, container_base_path: str = "/mnt/skills") -> str:
        """
        获取该技能在容器内的完整目录路径。

        参数：
            container_base_path: 技能在容器中挂载的基础路径。

        返回：
            技能目录在容器中的完整路径。
        """
        category_base = f"{container_base_path}/{self.category}"
        skill_path = self.skill_path
        if skill_path:
            return f"{category_base}/{skill_path}"
        return category_base

    def get_container_file_path(self, container_base_path: str = "/mnt/skills") -> str:
        """
        获取该技能主文件（SKILL.md）在容器内的完整路径。

        参数：
            container_base_path: 技能在容器中挂载的基础路径。

        返回：
            技能 SKILL.md 在容器中的完整路径。
        """
        return f"{self.get_container_path(container_base_path)}/SKILL.md"

    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, description={self.description!r}, category={self.category!r})"
