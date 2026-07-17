"""自定义代理的增删改查接口。"""

import logging
import re
import shutil

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config.agents_config import AgentConfig, list_custom_agents, load_agent_config, load_agent_soul
from src.config.paths import get_paths

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agents"])

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class AgentResponse(BaseModel):
    """自定义 Agent 响应模型。"""

    name: str = Field(..., description="Agent name (hyphen-case)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    soul: str | None = Field(default=None, description="SOUL.md content (included on GET /{name})")


class AgentsListResponse(BaseModel):
    """自定义 Agent 列表响应模型。"""

    agents: list[AgentResponse]


class AgentCreateRequest(BaseModel):
    """创建自定义 Agent 的请求体。"""

    name: str = Field(..., description="Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    soul: str = Field(default="", description="SOUL.md content — agent personality and behavioral guardrails")


class AgentUpdateRequest(BaseModel):
    """更新自定义 Agent 的请求体。"""

    description: str | None = Field(default=None, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    soul: str | None = Field(default=None, description="Updated SOUL.md content")


def _validate_agent_name(name: str) -> None:
    """校验 Agent 名称是否合法。

    参数：
        name: 待校验的 Agent 名称。

    异常：
        HTTPException: 名称非法时抛出 422。
    """
    if AGENT_NAME_PATTERN.fullmatch(name) is None:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid agent name '{name}'. Must match ^[A-Za-z0-9-]+$ (letters, digits, and hyphens only).",
        )


def _normalize_agent_name(name: str) -> str:
    """将 Agent 名称标准化为小写，用于文件系统存储。"""
    return name.lower()


def _agent_config_to_response(agent_cfg: AgentConfig, include_soul: bool = False) -> AgentResponse:
    """将 `AgentConfig` 转换为 `AgentResponse`。"""
    soul: str | None = None
    if include_soul:
        soul = load_agent_soul(agent_cfg.name) or ""

    return AgentResponse(
        name=agent_cfg.name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        tool_groups=agent_cfg.tool_groups,
        soul=soul,
    )


@router.get(
    "/agents",
    response_model=AgentsListResponse,
    summary="List Custom Agents",
    description="List all custom agents available in the agents directory.",
)
async def list_agents() -> AgentsListResponse:
    """列出所有自定义 Agent（不包含 soul 内容）。"""
    try:
        agents = list_custom_agents()
        return AgentsListResponse(agents=[_agent_config_to_response(a) for a in agents])
    except Exception as exc:
        logger.error("Failed to list agents (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to list agents") from exc


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(name: str) -> dict:
    """检查 Agent 名称是否可用（不区分大小写）。

    参数：
        name: 待检查名称。

    返回：
        ``{"available": true/false, "name": "<normalized>"}``

    异常：
        HTTPException: 名称非法时抛出 422。
    """
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    available = not get_paths().agent_dir(normalized).exists()
    return {"available": available, "name": normalized}


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and SOUL.md content for a specific custom agent.",
)
async def get_agent(name: str) -> AgentResponse:
    """获取指定 Agent 详情（包含 SOUL.md 内容）。

    参数：
        name: Agent 名称。

    返回：
        Agent 详细信息。

    异常：
        HTTPException: Agent 不存在时抛出 404。
    """
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    try:
        agent_cfg = load_agent_config(name)
        return _agent_config_to_response(agent_cfg, include_soul=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as exc:
        logger.error("Failed to get agent (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to get agent") from exc


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create Custom Agent",
    description="Create a new custom agent with its config and SOUL.md.",
)
async def create_agent_endpoint(request: AgentCreateRequest) -> AgentResponse:
    """创建新的自定义 Agent。

    参数：
        request: 创建请求体。

    返回：
        创建后的 Agent 详情。

    异常：
        HTTPException: Agent 已存在时抛出 409；名称非法时抛出 422。
    """
    _validate_agent_name(request.name)
    normalized_name = _normalize_agent_name(request.name)

    agent_dir = get_paths().agent_dir(normalized_name)

    if agent_dir.exists():
        raise HTTPException(status_code=409, detail=f"Agent '{normalized_name}' already exists")

    try:
        agent_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{normalized_name}' already exists",
        ) from exc

    created_agent_dir = True
    try:
        # 写入 config.yaml
        config_data: dict = {"name": normalized_name}
        if request.description:
            config_data["description"] = request.description
        if request.model is not None:
            config_data["model"] = request.model
        if request.tool_groups is not None:
            config_data["tool_groups"] = request.tool_groups

        config_file = agent_dir / "config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        # 写入 SOUL.md
        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(request.soul, encoding="utf-8")

        logger.info("Created agent definition")

        agent_cfg = load_agent_config(normalized_name)
        return _agent_config_to_response(agent_cfg, include_soul=True)

    except HTTPException:
        raise
    except Exception as exc:
        # 失败时清理已创建目录
        if created_agent_dir and agent_dir.exists():
            shutil.rmtree(agent_dir, ignore_errors=True)
        logger.error("Failed to create agent (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to create agent") from exc


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or SOUL.md.",
)
async def update_agent(name: str, request: AgentUpdateRequest) -> AgentResponse:
    """更新已有自定义 Agent 的配置与/或 SOUL.md。

    参数：
        name: Agent 名称。
        request: 更新请求（字段均可选）。

    返回：
        更新后的 Agent 详情。

    异常：
        HTTPException: Agent 不存在时抛出 404。
    """
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    try:
        agent_cfg = load_agent_config(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    agent_dir = get_paths().agent_dir(name)

    try:
        # 若配置字段有变更，则更新 config.yaml
        config_changed = any(v is not None for v in [request.description, request.model, request.tool_groups])

        if config_changed:
            updated: dict = {
                "name": agent_cfg.name,
                "description": request.description if request.description is not None else agent_cfg.description,
            }
            new_model = request.model if request.model is not None else agent_cfg.model
            if new_model is not None:
                updated["model"] = new_model

            new_tool_groups = request.tool_groups if request.tool_groups is not None else agent_cfg.tool_groups
            if new_tool_groups is not None:
                updated["tool_groups"] = new_tool_groups

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(updated, f, default_flow_style=False, allow_unicode=True)

        # 若提供 soul，则更新 SOUL.md
        if request.soul is not None:
            soul_path = agent_dir / "SOUL.md"
            soul_path.write_text(request.soul, encoding="utf-8")

        logger.info(f"Updated agent '{name}'")

        refreshed_cfg = load_agent_config(name)
        return _agent_config_to_response(refreshed_cfg, include_soul=True)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update agent (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to update agent") from exc


class UserProfileResponse(BaseModel):
    """Legacy/operator USER.md response model."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Update request for the non-injected legacy/operator USER.md."""

    content: str = Field(default="", description="USER.md content — describes the user's background and preferences")


@router.get(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Get User Profile",
    description="Read legacy/operator USER.md state; it is not injected into prompts.",
)
async def get_user_profile() -> UserProfileResponse:
    """读取全局 USER.md。

    返回：
        `UserProfileResponse`；若 USER.md 不存在则 `content=None`。
    """
    try:
        user_md_path = get_paths().user_md_file
        if not user_md_path.exists():
            return UserProfileResponse(content=None)
        raw = user_md_path.read_text(encoding="utf-8").strip()
        return UserProfileResponse(content=raw or None)
    except Exception as exc:
        logger.error("Failed to read user profile (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to read user profile") from exc


@router.put(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Update User Profile",
    description="Write legacy/operator USER.md state; it is not injected into prompts.",
)
async def update_user_profile(request: UserProfileUpdateRequest) -> UserProfileResponse:
    """更新全局 USER.md。

    参数：
        request: 包含新 USER.md 内容的请求体。

    返回：
        保存后的内容。
    """
    try:
        paths = get_paths()
        paths.base_dir.mkdir(parents=True, exist_ok=True)
        paths.user_md_file.write_text(request.content, encoding="utf-8")
        logger.info("Updated legacy user profile")
        return UserProfileResponse(content=request.content or None)
    except Exception as exc:
        logger.error("Failed to update user profile (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to update user profile") from exc


@router.delete(
    "/agents/{name}",
    status_code=204,
    summary="Delete Custom Agent",
    description=(
        "Delete the custom Agent definition directory. Tenant-scoped memory "
        "partitions are retained and require a separate purge policy."
    ),
)
async def delete_agent(name: str) -> None:
    """删除指定自定义 Agent（含其全部文件）。

    参数：
        name: Agent 名称。

    异常：
        HTTPException: Agent 不存在时抛出 404。
    """
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    agent_dir = get_paths().agent_dir(name)

    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        shutil.rmtree(agent_dir)
        logger.info("Deleted agent definition")
    except Exception as exc:
        logger.error("Failed to delete agent (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to delete agent") from exc
