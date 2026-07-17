import logging
import os
from pathlib import Path

from .parser import parse_skill_file
from .types import Skill

logger = logging.getLogger(__name__)


def get_skills_root_path() -> Path:
    """获取 skills 目录根路径。

    返回：
        skills 目录路径（`lumen/skills`）。
    """
    # 后端目录（backend）：当前文件向上三级
    backend_dir = Path(__file__).resolve().parent.parent.parent
    # 技能目录（skills）：backend 的同级目录
    skills_dir = backend_dir.parent / "skills"
    return skills_dir


def load_skills(skills_path: Path | None = None, use_config: bool = True, enabled_only: bool = False) -> list[Skill]:
    """从 skills 目录加载全部技能。

    会扫描 `public` 与 `custom` 两个目录，解析 `SKILL.md` 提取元数据。
    启用状态由扩展配置文件控制。

    参数：
        skills_path: 可选的 skills 目录自定义路径。
            若未提供且 `use_config=True`，则从配置读取；
            否则默认使用 `lumen/skills`。
        use_config: 是否从配置加载 skills 路径（默认 True）。
        enabled_only: 若为 True，仅返回启用技能（默认 False）。

    返回：
        按名称排序的 Skill 对象列表。
    """
    if skills_path is None:
        if use_config:
            try:
                from src.config import get_app_config

                config = get_app_config()
                skills_path = config.skills.get_skills_path()
            except Exception:
                # 配置加载失败时回退到默认路径
                skills_path = get_skills_root_path()
        else:
            skills_path = get_skills_root_path()

    if not skills_path.exists():
        return []

    skills = []
    skill_names: set[str] = set()

    # 扫描 public/custom 两个目录
    for category in ["public", "custom"]:
        category_path = skills_path / category
        if not category_path.exists() or not category_path.is_dir():
            continue

        for current_root, dir_names, file_names in os.walk(category_path):
            # 保持遍历顺序稳定，并跳过隐藏目录。
            dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
            if "SKILL.md" not in file_names:
                continue

            skill_file = Path(current_root) / "SKILL.md"
            relative_path = skill_file.parent.relative_to(category_path)

            skill = parse_skill_file(skill_file, category=category, relative_path=relative_path)
            if skill:
                if skill.name in skill_names:
                    raise ValueError(f"Duplicate skill name: {skill.name}")
                skill_names.add(skill.name)
                skills.append(skill)

    # 加载技能状态配置并更新 enabled 字段
    # 注意：这里使用 ExtensionsConfig.from_file() 而不是 get_extensions_config()，
    # 目的是始终从磁盘读取最新配置，确保 Gateway API（独立进程）写入的变更
    # 能在 LangGraph Server 侧加载技能时立即生效。
    try:
        from src.config.extensions_config import ExtensionsConfig

        extensions_config = ExtensionsConfig.from_file()
        for skill in skills:
            skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
    except Exception as exc:
        # 配置加载失败时默认全部启用
        logger.warning(
            "Failed to load skill enablement state; using defaults (error_type=%s)",
            type(exc).__name__,
        )

    # 按需过滤启用状态
    if enabled_only:
        skills = [skill for skill in skills if skill.enabled]

    # 统一按名称排序，保证结果稳定
    skills.sort(key=lambda s: s.name)

    return skills
