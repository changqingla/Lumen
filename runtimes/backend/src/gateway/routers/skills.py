import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config.extensions_config import reload_extensions_config, update_raw_extensions_config
from src.config.paths import get_paths
from src.skills import Skill, load_skills
from src.skills.archive_installer import InvalidSkillArchiveError, SkillAlreadyExistsError, install_skill_archive
from src.skills.loader import get_skills_root_path
from src.utils.thread_files import (
    ThreadFileAccessError,
    ThreadFileChangedError,
    ThreadFileNotFoundError,
    ThreadFileNotRegularError,
    ThreadFileTooLargeError,
    resolve_thread_file,
    snapshot_thread_file_async,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["skills"])

_SKILL_ARCHIVE_SOURCE_MAX_BYTES = 100 * 1024 * 1024


class SkillResponse(BaseModel):
    """技能信息响应模型。"""

    name: str = Field(..., description="技能名称")
    description: str = Field(..., description="技能功能描述")
    license: str | None = Field(None, description="许可证信息")
    category: str = Field(..., description="技能分类（public 或 custom）")
    enabled: bool = Field(default=True, description="该技能是否启用")


class SkillsListResponse(BaseModel):
    """技能列表响应模型。"""

    skills: list[SkillResponse]


class SkillUpdateRequest(BaseModel):
    """更新技能请求模型。"""

    enabled: bool = Field(..., description="是否启用该技能")


class SkillInstallRequest(BaseModel):
    """从 `.skill` 文件安装技能的请求模型。"""

    thread_id: str = Field(..., description=".skill 文件所在线程 ID")
    path: str = Field(..., description=".skill 文件的虚拟路径（例如 mnt/user-data/outputs/my-skill.skill）")


class SkillInstallResponse(BaseModel):
    """技能安装响应模型。"""

    success: bool = Field(..., description="安装是否成功")
    skill_name: str = Field(..., description="已安装技能名称")
    message: str = Field(..., description="安装结果消息")


def _skill_to_response(skill: Skill) -> SkillResponse:
    """将 Skill 对象转换为 SkillResponse。"""
    return SkillResponse(
        name=skill.name,
        description=skill.description,
        license=skill.license,
        category=skill.category,
        enabled=skill.enabled,
    )


@router.get(
    "/skills",
    response_model=SkillsListResponse,
    summary="列出全部技能",
    description="读取 public 与 custom 目录中的全部技能列表。",
)
async def list_skills() -> SkillsListResponse:
    """获取技能列表（包含已禁用技能）。

    返回：
        带元数据的技能列表。
    """
    try:
        # 加载全部技能（包含禁用项）
        skills = load_skills(enabled_only=False)
        return SkillsListResponse(skills=[_skill_to_response(skill) for skill in skills])
    except Exception as exc:
        logger.error("加载技能失败（%s）", type(exc).__name__)
        raise HTTPException(status_code=500, detail="加载技能失败") from exc


@router.get(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="获取技能详情",
    description="按技能名称读取指定技能的详细信息。",
)
async def get_skill(skill_name: str) -> SkillResponse:
    """获取指定技能详情。

    参数：
        skill_name: 要查询的技能名称。

    返回：
        找到时返回技能信息。

    异常：
        HTTPException: 技能不存在时返回 404。
    """
    try:
        skills = load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        return _skill_to_response(skill)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("获取技能失败（%s）", type(exc).__name__)
        raise HTTPException(status_code=500, detail="获取技能失败") from exc


@router.put(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="更新技能状态",
    description="通过修改 extensions_config.json 更新技能启用状态。",
)
async def update_skill(skill_name: str, request: SkillUpdateRequest) -> SkillResponse:
    """更新技能启用状态。

    该操作会修改 `extensions_config.json` 中的状态配置，
    不会修改技能目录里的 `SKILL.md` 本体。

    参数：
        skill_name: 要更新的技能名称。
        request: 包含新启用状态的请求体。

    返回：
        更新后的技能信息。

    异常：
        HTTPException: 技能不存在时返回 404；更新失败时返回 500。
    """
    try:
        # 先确认技能存在
        skills = load_skills(enabled_only=False)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        # 读取未解析的原始 JSON，避免把 `$ENV_VAR` 对应的秘密明文写回磁盘。
        def apply_update(config_data: dict) -> None:
            skill_states = config_data.get("skills", {})
            if not isinstance(skill_states, dict):
                skill_states = {}
            skill_states[skill_name] = {"enabled": request.enabled}
            config_data["skills"] = skill_states

        update_raw_extensions_config(apply_update)

        logger.info("技能配置已更新")

        # 重载 extensions 配置以刷新全局缓存
        reload_extensions_config()

        # 重新加载技能，获取更新后的状态用于 API 返回
        skills = load_skills(enabled_only=False)
        updated_skill = next((s for s in skills if s.name == skill_name), None)

        if updated_skill is None:
            raise HTTPException(status_code=500, detail=f"更新后重新加载技能 '{skill_name}' 失败")

        logger.info(f"技能 '{skill_name}' 的启用状态已更新为 {request.enabled}")
        return _skill_to_response(updated_skill)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("更新技能失败（%s）", type(exc).__name__)
        raise HTTPException(status_code=500, detail="更新技能失败") from exc


@router.post(
    "/skills/install",
    response_model=SkillInstallResponse,
    summary="安装技能",
    description="从线程用户目录中的 .skill 文件（ZIP 压缩包）安装技能。",
)
async def install_skill(request: SkillInstallRequest) -> SkillInstallResponse:
    """从 `.skill` 文件安装技能。

    `.skill` 本质是 ZIP 压缩包，通常包含技能目录、`SKILL.md`，
    以及可选资源（scripts、references、assets 等）。

    参数：
        request: 安装请求，包含 thread_id 与 `.skill` 的虚拟路径。

    返回：
        安装结果（含技能名称与状态消息）。

    异常：
        HTTPException:
            - 400：路径无效或不是合法 `.skill` 文件
            - 403：访问被拒绝（检测到路径穿越）
            - 404：文件不存在
            - 409：技能已存在
            - 500：安装失败
    """
    try:
        if Path(request.path).suffix != ".skill":
            raise HTTPException(status_code=400, detail="File must have .skill extension")

        try:
            resolved = resolve_thread_file(
                get_paths(),
                request.thread_id,
                request.path,
            )
        except ThreadFileAccessError as exc:
            raise HTTPException(status_code=403, detail="Access denied") from exc

        snapshot = None
        try:
            snapshot = await snapshot_thread_file_async(
                resolved,
                max_bytes=_SKILL_ARCHIVE_SOURCE_MAX_BYTES,
                suffix=".skill",
            )
            installed = await asyncio.to_thread(
                install_skill_archive,
                snapshot.path,
                get_skills_root_path(),
            )
        except ThreadFileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Skill file not found") from exc
        except ThreadFileNotRegularError as exc:
            raise HTTPException(status_code=400, detail="Skill path is not a file") from exc
        except ThreadFileTooLargeError as exc:
            raise HTTPException(status_code=413, detail="Skill archive exceeds the size limit") from exc
        except ThreadFileChangedError as exc:
            raise HTTPException(status_code=409, detail="Skill archive changed while being read") from exc
        except ThreadFileAccessError as exc:
            raise HTTPException(status_code=403, detail="Access denied") from exc
        except InvalidSkillArchiveError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SkillAlreadyExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Skill '{exc.skill_name}' already exists. Please remove it first or use a different name.",
            ) from exc
        finally:
            if snapshot is not None:
                snapshot.cleanup()

        logger.info("技能 '%s' 已成功安装", installed.name)
        return SkillInstallResponse(success=True, skill_name=installed.name, message=f"技能 '{installed.name}' 安装成功")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("安装技能失败（%s）", type(exc).__name__)
        raise HTTPException(status_code=500, detail="安装技能失败") from exc
