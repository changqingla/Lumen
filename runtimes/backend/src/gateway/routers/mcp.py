import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.config.extensions_config import (
    ExtensionsConfig,
    McpOAuthConfig,
    McpServerConfig,
    get_extensions_config,
    reload_extensions_config,
    update_raw_extensions_config,
)
from src.config.extensions_secrets import (
    redact_mcp_configuration,
    restore_mcp_server_secrets,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["mcp"])


class McpOAuthConfigResponse(McpOAuthConfig):
    """模型上下文协议（MCP）服务的 OAuth 配置模型。"""

    model_config = ConfigDict(extra="allow")


class McpServerConfigResponse(McpServerConfig):
    """模型上下文协议（MCP）服务配置响应模型。"""

    oauth: McpOAuthConfigResponse | None = Field(default=None, description="MCP HTTP/SSE 服务的 OAuth 配置")
    model_config = ConfigDict(extra="allow")


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


def _config_to_response(config: ExtensionsConfig) -> McpConfigResponse:
    return McpConfigResponse.model_validate(
        {"mcp_servers": redact_mcp_configuration(config)}
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

    return _config_to_response(config)


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
        def apply_update(config_data: dict) -> None:
            existing_servers = config_data.get("mcpServers", {})
            if not isinstance(existing_servers, dict):
                existing_servers = {}
            config_data["mcpServers"] = {
                name: restore_mcp_server_secrets(
                    server.model_dump(),
                    existing_servers.get(name, {}) if isinstance(existing_servers.get(name), dict) else {},
                )
                for name, server in request.mcp_servers.items()
            }

        update_raw_extensions_config(apply_update)

        logger.info("MCP 配置已更新")

        # 注意：无需在此处手动重置 MCP 工具缓存。
        # 图编排服务进程（LangGraph Server，独立进程）会通过 mtime 变更自动触发重新初始化。

        # 重载配置并更新全局缓存
        reloaded_config = reload_extensions_config()
        return _config_to_response(reloaded_config)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("更新 MCP 配置失败（%s）", type(exc).__name__)
        raise HTTPException(status_code=500, detail="更新 MCP 配置失败") from exc
