"""使用 langchain-mcp-adapters 的 MCP 客户端配置构建工具。"""

import logging
from typing import Any

from src.config.extensions_config import ExtensionsConfig, McpServerConfig

logger = logging.getLogger(__name__)


def build_server_params(server_name: str, config: McpServerConfig) -> dict[str, Any]:
    """构建单个 MCP 服务的连接参数。

    参数：
        server_name: MCP 服务名称。
        config: MCP 服务配置。

    返回：
        供 langchain-mcp-adapters 使用的服务参数字典。
    """
    transport_type = config.type or "stdio"
    params: dict[str, Any] = {"transport": transport_type}

    if transport_type == "stdio":
        if not config.command:
            raise ValueError(f"MCP server '{server_name}' with stdio transport requires 'command' field")
        params["command"] = config.command
        params["args"] = config.args
        # 若存在环境变量则附加
        if config.env:
            params["env"] = config.env
    elif transport_type in ("sse", "http"):
        if not config.url:
            raise ValueError(f"MCP server '{server_name}' with {transport_type} transport requires 'url' field")
        params["url"] = config.url
        # 若存在请求头则附加
        if config.headers:
            params["headers"] = config.headers
    else:
        raise ValueError(f"MCP server '{server_name}' has unsupported transport type: {transport_type}")

    return params


def build_servers_config(extensions_config: ExtensionsConfig) -> dict[str, dict[str, Any]]:
    """构建全部启用 MCP 服务的参数映射。

    参数：
        extensions_config: 包含所有 MCP 服务配置的扩展配置对象。

    返回：
        以服务名为键、服务参数为值的字典。
    """
    enabled_servers = extensions_config.get_enabled_mcp_servers()

    if not enabled_servers:
        logger.info("No enabled MCP servers found")
        return {}

    servers_config = {}
    for server_name, server_config in enabled_servers.items():
        try:
            servers_config[server_name] = build_server_params(server_name, server_config)
            logger.info(f"Configured MCP server: {server_name}")
        except Exception as e:
            logger.error(f"Failed to configure MCP server '{server_name}': {e}")

    return servers_config
