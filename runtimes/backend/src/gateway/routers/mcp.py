import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config.extensions_config import ExtensionsConfig, get_extensions_config, reload_extensions_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["mcp"])


class McpOAuthConfigResponse(BaseModel):
    """模型上下文协议（MCP）服务的 OAuth 配置模型。"""

    enabled: bool = Field(default=True, description="是否启用 OAuth 令牌注入")
    token_url: str = Field(default="", description="OAuth token 端点 URL")
    grant_type: Literal["client_credentials", "refresh_token"] = Field(default="client_credentials", description="OAuth 授权类型")
    client_id: str | None = Field(default=None, description="OAuth 客户端 ID")
    client_secret: str | None = Field(default=None, description="OAuth 客户端密钥")
    refresh_token: str | None = Field(default=None, description="OAuth 刷新令牌")
    scope: str | None = Field(default=None, description="OAuth 作用域（scope）")
    audience: str | None = Field(default=None, description="OAuth audience 值")
    token_field: str = Field(default="access_token", description="响应中 access token 所在字段")
    token_type_field: str = Field(default="token_type", description="响应中 token 类型所在字段")
    expires_in_field: str = Field(default="expires_in", description="响应中有效期秒数字段")
    default_token_type: str = Field(default="Bearer", description="响应缺失 token_type 时使用的默认值")
    refresh_skew_seconds: int = Field(default=60, description="在到期前多少秒触发刷新")
    extra_token_params: dict[str, str] = Field(default_factory=dict, description="发送给 token 端点的额外表单参数")


class McpServerConfigResponse(BaseModel):
    """模型上下文协议（MCP）服务配置响应模型。"""

    enabled: bool = Field(default=True, description="是否启用该 MCP 服务")
    type: str = Field(default="stdio", description="传输类型：`stdio`、`sse` 或 `http`")
    command: str | None = Field(default=None, description="启动 MCP 服务的命令（stdio 类型）")
    args: list[str] = Field(default_factory=list, description="传给启动命令的参数（stdio 类型）")
    env: dict[str, str] = Field(default_factory=dict, description="MCP 服务环境变量")
    url: str | None = Field(default=None, description="MCP 服务地址（sse 或 http 类型）")
    headers: dict[str, str] = Field(default_factory=dict, description="请求头（sse 或 http 类型）")
    oauth: McpOAuthConfigResponse | None = Field(default=None, description="MCP HTTP/SSE 服务的 OAuth 配置")
    description: str = Field(default="", description="该 MCP 服务能力的人类可读说明")


class McpConfigResponse(BaseModel):
    """模型上下文协议（MCP）配置响应模型。"""

    mcp_servers: dict[str, McpServerConfigResponse] = Field(
        default_factory=dict,
        description="MCP 服务名到配置的映射",
    )


class McpConfigUpdateRequest(BaseModel):
    """更新 MCP 配置的请求体模型。"""

    mcp_servers: dict[str, McpServerConfigResponse] = Field(
        ...,
        description="MCP 服务名到配置的映射",
    )


@router.get(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="获取 MCP 配置",
    description="读取当前 Model Context Protocol（MCP）服务配置。",
)
async def get_mcp_configuration() -> McpConfigResponse:
    """获取当前 MCP 配置。

    返回：
        包含全部 MCP 服务配置的响应对象。
    """
    config = get_extensions_config()

    return McpConfigResponse(mcp_servers={name: McpServerConfigResponse(**server.model_dump()) for name, server in config.mcp_servers.items()})


@router.put(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="更新 MCP 配置",
    description="更新 Model Context Protocol（MCP）服务配置并写入文件。",
)
async def update_mcp_configuration(request: McpConfigUpdateRequest) -> McpConfigResponse:
    """更新 MCP 配置并写入配置文件。

    主要流程：
    1. 将新配置写入 `extensions_config.json`
    2. 重载配置缓存
    3. 返回更新后的 MCP 配置

    参数：
        request: 待保存的 MCP 配置。

    返回：
        更新后的 MCP 配置。

    异常：
        HTTPException: 配置写入失败时返回 500。
    """
    try:
        # 获取当前配置路径（若不存在则确定新写入位置）
        config_path = ExtensionsConfig.resolve_config_path()

        # 若无现有配置文件，则在父目录（项目根）创建
        if config_path is None:
            config_path = Path.cwd().parent / "extensions_config.json"
            logger.info(f"未找到现有 extensions 配置，将在此创建新文件：{config_path}")

        # 读取当前配置，保留 skills 部分不被覆盖
        current_config = get_extensions_config()

        # 转为可 JSON 序列化的数据结构
        config_data = {
            "mcpServers": {name: server.model_dump() for name, server in request.mcp_servers.items()},
            "skills": {name: {"enabled": skill.enabled} for name, skill in current_config.skills.items()},
        }

        # 写回配置文件
        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)

        logger.info(f"MCP 配置已更新并写入：{config_path}")

        # 注意：无需在此处手动重置 MCP 工具缓存。
        # 图编排服务进程（LangGraph Server，独立进程）会通过 mtime 变更自动触发重新初始化。

        # 重载配置并更新全局缓存
        reloaded_config = reload_extensions_config()
        return McpConfigResponse(mcp_servers={name: McpServerConfigResponse(**server.model_dump()) for name, server in reloaded_config.mcp_servers.items()})

    except Exception as e:
        logger.error(f"更新 MCP 配置失败：{e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新 MCP 配置失败：{str(e)}")
