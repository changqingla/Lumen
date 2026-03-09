"""MCP Client Manager for managing multiple MCP server connections.

This module provides the MCPClientManager class for coordinating
connections to multiple MCP servers with connection pooling support.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from .client import MCPClient, MCPConnectionError
from .config import MCPConfigManager, MCPServerConfig
from .connection_pool import MCPConnectionPool, DEFAULT_POOL_SIZE, DEFAULT_MAX_POOL_SIZE
from .models import MCPTool, MCPToolResult, ServerStatus

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manager for multiple MCP server connections with pooling.
    
    Coordinates the lifecycle of multiple MCP connection pools and provides
    a unified interface for tool discovery and invocation.
    
    Attributes:
        config_path: Path to the MCP servers configuration file.
        pools: Dictionary mapping server IDs to MCPConnectionPool instances.
        tools: Dictionary mapping tool names to MCPTool objects.
    """
    
    def __init__(
        self,
        config_path: str = "config/mcp_servers.json",
        pool_min_size: int = DEFAULT_POOL_SIZE,
        pool_max_size: int = DEFAULT_MAX_POOL_SIZE
    ):
        """Initialize the client manager.
        
        Args:
            config_path: Path to the MCP servers configuration file.
            pool_min_size: Minimum connections per server pool (default: 3).
            pool_max_size: Maximum connections per server pool (default: 5).
        """
        self.config_path = config_path
        self.config_manager = MCPConfigManager(config_path)
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.pools: Dict[str, MCPConnectionPool] = {}
        self.tools: Dict[str, MCPTool] = {}
        self._tool_to_server: Dict[str, str] = {}
    
    async def initialize(self) -> None:
        """Initialize all configured MCP server connection pools.
        
        Loads configuration and creates connection pools for all enabled servers.
        Failed initializations are logged but don't prevent other servers
        from connecting.
        """
        self.config_manager.load_config()
        enabled_configs = self.config_manager.get_enabled_configs()
        
        if not enabled_configs:
            logger.warning("No enabled MCP servers found in configuration")
            return
        
        logger.info(
            f"Initializing {len(enabled_configs)} MCP server pools "
            f"(min={self.pool_min_size}, max={self.pool_max_size})..."
        )
        
        # Initialize all pools concurrently
        tasks = [
            self._init_pool(server_id, config)
            for server_id, config in enabled_configs.items()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log results
        successful = sum(1 for r in results if r is True)
        logger.info(
            f"MCP initialization complete: {successful}/{len(enabled_configs)} "
            f"server pools initialized"
        )
    
    async def _init_pool(
        self,
        server_id: str,
        config: MCPServerConfig
    ) -> bool:
        """Initialize a connection pool for a single server.
        
        Args:
            server_id: Unique identifier for the server.
            config: Server configuration.
            
        Returns:
            True if pool initialization succeeded.
        """
        try:
            # Use per-server pool config if available, otherwise use manager defaults
            min_size = config.pool_min_size if config.pool_min_size else self.pool_min_size
            max_size = config.pool_max_size if config.pool_max_size else self.pool_max_size
            
            pool = MCPConnectionPool(
                server_id=server_id,
                config=config,
                min_size=min_size,
                max_size=max_size
            )
            
            if await pool.initialize():
                self.pools[server_id] = pool
                
                # Register tools from this pool
                for tool in pool.tools:
                    self._register_tool(tool)
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to initialize pool for '{server_id}': {e}")
            return False
    
    async def disconnect_server(self, server_id: str) -> None:
        """Disconnect and close a specific server's pool.
        
        Args:
            server_id: The server identifier to disconnect.
        """
        if server_id not in self.pools:
            logger.warning(f"Server pool '{server_id}' not found")
            return
        
        pool = self.pools[server_id]
        
        # Unregister tools from this server
        self._unregister_server_tools(server_id)
        
        # Close the pool
        await pool.close()
        del self.pools[server_id]
        
        logger.info(f"Disconnected server pool '{server_id}'")
    
    async def disconnect_all(self) -> None:
        """Disconnect and close all server pools."""
        server_ids = list(self.pools.keys())
        for server_id in server_ids:
            await self.disconnect_server(server_id)
    
    def _register_tool(self, tool: MCPTool) -> None:
        """Register a tool, handling name collisions.
        
        Args:
            tool: The MCPTool to register.
        """
        tool_name = tool.name
        
        # Check for name collision
        if tool_name in self.tools:
            existing_server = self._tool_to_server.get(tool_name)
            if existing_server != tool.server_id:
                # Prefix with server ID to avoid collision
                tool_name = f"{tool.server_id}_{tool.name}"
                logger.warning(
                    f"Tool name collision for '{tool.name}', "
                    f"registering as '{tool_name}'"
                )
        
        self.tools[tool_name] = tool
        self._tool_to_server[tool_name] = tool.server_id
    
    def _unregister_server_tools(self, server_id: str) -> None:
        """Remove all tools from a specific server.
        
        Args:
            server_id: The server whose tools should be removed.
        """
        tools_to_remove = [
            name for name, sid in self._tool_to_server.items()
            if sid == server_id
        ]
        
        for tool_name in tools_to_remove:
            del self.tools[tool_name]
            del self._tool_to_server[tool_name]
        
        logger.info(
            f"Unregistered {len(tools_to_remove)} tools from server '{server_id}'"
        )
    
    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> MCPToolResult:
        """Call an MCP tool by name using a pooled connection.
        
        Routes the call to the appropriate server pool.
        
        Args:
            tool_name: Name of the tool to call.
            arguments: Dictionary of tool arguments.
            
        Returns:
            MCPToolResult containing the result or error.
        """
        if tool_name not in self.tools:
            return MCPToolResult(
                success=False,
                content=None,
                error=f"Tool '{tool_name}' not found"
            )
        
        server_id = self._tool_to_server[tool_name]
        
        if server_id not in self.pools:
            return MCPToolResult(
                success=False,
                content=None,
                error=f"Server pool '{server_id}' not available"
            )
        
        pool = self.pools[server_id]
        
        # Get the original tool name (without server prefix if added)
        tool = self.tools[tool_name]
        original_name = tool.name
        
        return await pool.call_tool(original_name, arguments)
    
    def get_available_tools(self) -> List[MCPTool]:
        """Get all available tools from connected servers.
        
        Returns:
            List of MCPTool objects from all connected servers.
        """
        return list(self.tools.values())
    
    def get_tool(self, tool_name: str) -> Optional[MCPTool]:
        """Get a specific tool by name.
        
        Args:
            tool_name: The tool name to look up.
            
        Returns:
            The MCPTool if found, None otherwise.
        """
        return self.tools.get(tool_name)
    
    def get_server_status(self, server_id: str) -> Optional[ServerStatus]:
        """Get the status of a specific server pool.
        
        Args:
            server_id: The server identifier.
            
        Returns:
            ServerStatus.CONNECTED if pool is initialized, None if not found.
        """
        pool = self.pools.get(server_id)
        if pool and pool._initialized and not pool._closed:
            return ServerStatus.CONNECTED
        return None
    
    def get_connected_servers(self) -> List[str]:
        """Get list of connected server IDs.
        
        Returns:
            List of server IDs that have initialized pools.
        """
        return [
            server_id for server_id, pool in self.pools.items()
            if pool._initialized and not pool._closed
        ]
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get statistics for all connection pools.
        
        Returns:
            Dictionary with pool statistics for each server.
        """
        return {
            server_id: pool.get_stats()
            for server_id, pool in self.pools.items()
        }
