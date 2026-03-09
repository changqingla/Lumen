"""MCP (Model Context Protocol) integration module.

This module provides MCP client capabilities for connecting to and using
MCP servers like arxiv-mcp-server.
"""
from .models import (
    ServerStatus,
    MCPTool,
    MCPToolResult,
    ArxivPaper,
)
from .config import MCPServerConfig, MCPConfigManager
from .client import MCPClient
from .client_manager import MCPClientManager
from .connection_pool import MCPConnectionPool
from .tool_adapter import MCPToolAdapter

__all__ = [
    # Models
    "ServerStatus",
    "MCPTool",
    "MCPToolResult",
    "ArxivPaper",
    # Config
    "MCPServerConfig",
    "MCPConfigManager",
    # Client
    "MCPClient",
    "MCPClientManager",
    "MCPConnectionPool",
    # Adapter
    "MCPToolAdapter",
]
