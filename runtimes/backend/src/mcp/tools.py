"""使用 langchain-mcp-adapters 加载 MCP 工具。"""

import logging

from langchain_core.tools import BaseTool

from src.config.extensions_config import ExtensionsConfig
from src.mcp.client import build_servers_config
from src.mcp.oauth import build_oauth_tool_interceptor, get_initial_oauth_headers

logger = logging.getLogger(__name__)


async def get_mcp_tools() -> list[BaseTool]:
    """加载所有启用 MCP 服务暴露的工具。

    返回：
        来自各 MCP 服务的 LangChain 工具列表。
    """
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning("langchain-mcp-adapters not installed. Install it to enable MCP tools: pip install langchain-mcp-adapters")
        return []

    # 注意：这里使用 ExtensionsConfig.from_file() 而不是 get_extensions_config()，
    # 目的是始终从磁盘读取最新配置，确保 Gateway API（独立进程）写入的变更
    # 在初始化 MCP 工具时可立即生效。
    extensions_config = ExtensionsConfig.from_file()
    servers_config = build_servers_config(extensions_config)

    if not servers_config:
        logger.info("No enabled MCP servers configured")
        return []

    try:
        # 创建多服务 MCP 客户端
        logger.info(f"Initializing MCP client with {len(servers_config)} server(s)")

        # 为服务连接注入初始 OAuth 头（工具发现/会话初始化）
        initial_oauth_headers = await get_initial_oauth_headers(extensions_config)
        for server_name, auth_header in initial_oauth_headers.items():
            if server_name not in servers_config:
                continue
            if servers_config[server_name].get("transport") in ("sse", "http"):
                existing_headers = dict(servers_config[server_name].get("headers", {}))
                existing_headers["Authorization"] = auth_header
                servers_config[server_name]["headers"] = existing_headers

        tool_interceptors = []
        oauth_interceptor = build_oauth_tool_interceptor(extensions_config)
        if oauth_interceptor is not None:
            tool_interceptors.append(oauth_interceptor)

        client = MultiServerMCPClient(servers_config, tool_interceptors=tool_interceptors)

        # 拉取所有服务上的全部工具
        tools = await client.get_tools()
        logger.info(f"Successfully loaded {len(tools)} tool(s) from MCP servers")

        return tools

    except Exception as e:
        logger.error(f"Failed to load MCP tools: {e}", exc_info=True)
        return []
