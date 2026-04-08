from pathlib import Path

from pydantic import BaseModel, Field


class SkillsConfig(BaseModel):
    """技能系统配置。"""

    path: str | None = Field(
        default=None,
        description="技能目录路径。若未指定，默认为相对于 backend 目录的 ../skills",
    )
    container_path: str = Field(
        default="/mnt/skills",
        description="技能在沙箱容器中的挂载路径",
    )

    def get_skills_path(self) -> Path:
        """
        获取解析后的技能目录路径。

        返回：
            技能目录路径
        """
        if self.path:
            # 使用配置路径（可以是绝对路径或相对路径）
            path = Path(self.path)
            if not path.is_absolute():
                # 若为相对路径，则基于当前工作目录解析
                path = Path.cwd() / path
            return path.resolve()
        else:
            # 默认：相对于 backend 目录的 ../skills
            from src.skills.loader import get_skills_root_path

            return get_skills_root_path()

    def get_skill_container_path(self, skill_name: str, category: str = "public") -> str:
        """
        获取指定技能在容器内的完整路径。

        参数：
            skill_name: 技能名称（目录名）
            category: 技能类别（public 或 custom）

        返回：
            容器内技能完整路径
        """
        return f"{self.container_path}/{category}/{skill_name}"
