"""MCP Connection Pool for concurrent access.

This module provides connection pooling for MCP servers to support
multiple concurrent requests.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from .client import MCPClient, MCPConnectionError
from .config import MCPServerConfig
from .models import MCPTool, MCPToolResult, ServerStatus

logger = logging.getLogger(__name__)

# Default pool configuration
DEFAULT_POOL_SIZE = 3
DEFAULT_MAX_POOL_SIZE = 5
DEFAULT_ACQUIRE_TIMEOUT = 30.0


@dataclass
class PooledConnection:
    """A pooled MCP client connection.
    
    Attributes:
        client: The MCP client instance.
        in_use: Whether the connection is currently in use.
        created_at: Timestamp when the connection was created.
        last_used_at: Timestamp when the connection was last used.
        use_count: Number of times this connection has been used.
    """
    client: MCPClient
    in_use: bool = False
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    use_count: int = 0


class MCPConnectionPool:
    """Connection pool for a single MCP server.
    
    Manages multiple connections to the same MCP server to support
    concurrent tool calls.
    
    Attributes:
        server_id: Unique identifier for this server.
        config: Server configuration.
        min_size: Minimum number of connections to maintain.
        max_size: Maximum number of connections allowed.
        connections: List of pooled connections.
    """
    
    def __init__(
        self,
        server_id: str,
        config: MCPServerConfig,
        min_size: int = DEFAULT_POOL_SIZE,
        max_size: int = DEFAULT_MAX_POOL_SIZE
    ):
        """Initialize the connection pool.
        
        Args:
            server_id: Unique identifier for this server.
            config: Server configuration.
            min_size: Minimum connections to maintain (default: 3).
            max_size: Maximum connections allowed (default: 5).
        """
        self.server_id = server_id
        self.config = config
        self.min_size = min_size
        self.max_size = max_size
        self.connections: List[PooledConnection] = []
        self.tools: List[MCPTool] = []
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        self._initialized = False
        self._closed = False
    
    async def initialize(self) -> bool:
        """Initialize the connection pool.
        
        Creates the minimum number of connections and discovers tools.
        
        Returns:
            True if at least one connection was established.
        """
        if self._initialized:
            return True
        
        async with self._lock:
            if self._initialized:
                return True
            
            logger.info(
                f"Initializing connection pool for '{self.server_id}' "
                f"(min={self.min_size}, max={self.max_size})"
            )
            
            # Create initial connections concurrently
            tasks = [
                self._create_connection(i)
                for i in range(self.min_size)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count successful connections
            success_count = sum(
                1 for r in results
                if isinstance(r, PooledConnection)
            )
            
            if success_count == 0:
                logger.error(
                    f"Failed to create any connections for '{self.server_id}'"
                )
                return False
            
            # Discover tools from the first connection
            if self.connections:
                try:
                    raw_tools = await self.connections[0].client.discover_tools()
                    # Override server_id to use pool's server_id (without index)
                    self.tools = [
                        MCPTool(
                            name=t.name,
                            description=t.description,
                            input_schema=t.input_schema,
                            server_id=self.server_id  # Use pool's server_id
                        )
                        for t in raw_tools
                    ]
                except Exception as e:
                    logger.error(f"Failed to discover tools: {e}")
            
            self._initialized = True
            logger.info(
                f"Connection pool for '{self.server_id}' initialized: "
                f"{success_count}/{self.min_size} connections, "
                f"{len(self.tools)} tools discovered"
            )
            
            return True
    
    async def _create_connection(self, index: int = 0) -> Optional[PooledConnection]:
        """Create a new pooled connection.
        
        Args:
            index: Connection index for logging.
            
        Returns:
            PooledConnection if successful, None otherwise.
        """
        try:
            client = MCPClient(f"{self.server_id}_{index}", self.config)
            await client.connect()
            
            pooled = PooledConnection(client=client)
            self.connections.append(pooled)
            
            logger.debug(
                f"Created connection {index} for '{self.server_id}'"
            )
            return pooled
            
        except MCPConnectionError as e:
            logger.warning(
                f"Failed to create connection {index} for '{self.server_id}': {e}"
            )
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error creating connection {index}: {e}"
            )
            return None
    
    async def acquire(
        self,
        timeout: float = DEFAULT_ACQUIRE_TIMEOUT
    ) -> Optional[MCPClient]:
        """Acquire a connection from the pool.
        
        Waits for an available connection or creates a new one if
        the pool hasn't reached max_size.
        
        Args:
            timeout: Maximum time to wait for a connection.
            
        Returns:
            An MCPClient instance, or None if timeout exceeded.
        """
        if self._closed:
            logger.warning(f"Pool '{self.server_id}' is closed")
            return None
        
        deadline = time.time() + timeout
        
        async with self._condition:
            while True:
                # Check for available connection
                for pooled in self.connections:
                    if not pooled.in_use and pooled.client.status == ServerStatus.CONNECTED:
                        pooled.in_use = True
                        pooled.last_used_at = time.time()
                        pooled.use_count += 1
                        logger.debug(
                            f"Acquired connection from pool '{self.server_id}' "
                            f"(use_count={pooled.use_count})"
                        )
                        return pooled.client
                
                # Try to create new connection if under max_size
                if len(self.connections) < self.max_size:
                    pooled = await self._create_connection(len(self.connections))
                    if pooled:
                        pooled.in_use = True
                        pooled.use_count = 1
                        logger.debug(
                            f"Created new connection for pool '{self.server_id}' "
                            f"(total={len(self.connections)})"
                        )
                        return pooled.client
                
                # Wait for a connection to be released
                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning(
                        f"Timeout acquiring connection from pool '{self.server_id}'"
                    )
                    return None
                
                try:
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=remaining
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Timeout waiting for connection in pool '{self.server_id}'"
                    )
                    return None
    
    async def release(self, client: MCPClient) -> None:
        """Release a connection back to the pool.
        
        Args:
            client: The client to release.
        """
        async with self._condition:
            for pooled in self.connections:
                if pooled.client is client:
                    pooled.in_use = False
                    pooled.last_used_at = time.time()
                    logger.debug(
                        f"Released connection to pool '{self.server_id}'"
                    )
                    self._condition.notify()
                    return
            
            logger.warning(
                f"Attempted to release unknown client to pool '{self.server_id}'"
            )
    
    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: Optional[float] = None
    ) -> MCPToolResult:
        """Call a tool using a pooled connection.
        
        Automatically acquires and releases a connection.
        
        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments.
            timeout: Call timeout in seconds.
            
        Returns:
            MCPToolResult with the result or error.
        """
        client = await self.acquire()
        if not client:
            return MCPToolResult(
                success=False,
                content=None,
                error=f"Failed to acquire connection from pool '{self.server_id}'"
            )
        
        try:
            return await client.call_tool(tool_name, arguments, timeout)
        finally:
            await self.release(client)
    
    async def close(self) -> None:
        """Close all connections in the pool."""
        async with self._lock:
            self._closed = True
            
            for pooled in self.connections:
                try:
                    await pooled.client.disconnect()
                except Exception as e:
                    logger.warning(
                        f"Error disconnecting client in pool '{self.server_id}': {e}"
                    )
            
            self.connections.clear()
            self.tools.clear()
            self._initialized = False
            
            logger.info(f"Connection pool '{self.server_id}' closed")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics.
        
        Returns:
            Dictionary with pool statistics.
        """
        total = len(self.connections)
        in_use = sum(1 for p in self.connections if p.in_use)
        connected = sum(
            1 for p in self.connections
            if p.client.status == ServerStatus.CONNECTED
        )
        
        return {
            "server_id": self.server_id,
            "min_size": self.min_size,
            "max_size": self.max_size,
            "total_connections": total,
            "in_use": in_use,
            "available": connected - in_use,
            "connected": connected,
            "tools_count": len(self.tools),
            "initialized": self._initialized,
            "closed": self._closed
        }
