"""MCP Client for connecting to individual MCP servers.

This module provides the MCPClient class for managing connections
to a single MCP server.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import MCPServerConfig
from .models import MCPTool, MCPToolResult, ServerStatus

logger = logging.getLogger(__name__)


class MCPConnectionError(Exception):
    """MCP connection error."""
    
    def __init__(self, server_id: str, message: str, retry_count: int = 0):
        self.server_id = server_id
        self.retry_count = retry_count
        super().__init__(f"[{server_id}] {message} (retries: {retry_count})")


class MCPTimeoutError(MCPConnectionError):
    """MCP timeout error."""
    pass


class MCPToolError(Exception):
    """MCP tool call error."""
    
    def __init__(
        self,
        tool_name: str,
        message: str,
        original_error: Optional[Exception] = None
    ):
        self.tool_name = tool_name
        self.original_error = original_error
        super().__init__(f"Tool '{tool_name}' error: {message}")


class MCPClient:
    """Client for a single MCP server.
    
    Manages the connection lifecycle and tool interactions with
    an individual MCP server.
    
    Attributes:
        server_id: Unique identifier for this server.
        config: Server configuration.
        session: Active MCP client session.
        tools: List of discovered tools.
        status: Current connection status.
    """
    
    def __init__(self, server_id: str, config: MCPServerConfig):
        """Initialize the MCP client.
        
        Args:
            server_id: Unique identifier for this server.
            config: Server configuration object.
        """
        self.server_id = server_id
        self.config = config
        self.session: Optional[ClientSession] = None
        self.tools: List[MCPTool] = []
        self.status: ServerStatus = ServerStatus.DISCONNECTED
        self._read_stream = None
        self._write_stream = None
        self._context_manager = None
    
    async def connect(self, timeout: Optional[float] = None) -> bool:
        """Establish connection to the MCP server.
        
        Attempts to connect with retry logic using exponential backoff.
        
        Args:
            timeout: Connection timeout in seconds. Uses config default if None.
            
        Returns:
            True if connection succeeded, False otherwise.
            
        Raises:
            MCPTimeoutError: If connection times out after all retries.
            MCPConnectionError: If connection fails after all retries.
        """
        if timeout is None:
            timeout = self.config.timeout
        
        retry_count = self.config.retry_count
        last_error: Optional[Exception] = None
        
        for attempt in range(retry_count + 1):
            try:
                self.status = ServerStatus.CONNECTING
                logger.info(
                    f"Connecting to MCP server '{self.server_id}' "
                    f"(attempt {attempt + 1}/{retry_count + 1})"
                )
                
                await self._establish_connection(timeout)
                
                self.status = ServerStatus.CONNECTED
                logger.info(f"Connected to MCP server '{self.server_id}'")
                return True
                
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(
                    f"Connection to '{self.server_id}' timed out "
                    f"(attempt {attempt + 1})"
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Connection to '{self.server_id}' failed: {e} "
                    f"(attempt {attempt + 1})"
                )
            
            # Exponential backoff: 1s, 2s, 4s, ...
            if attempt < retry_count:
                delay = 2 ** attempt
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
        
        self.status = ServerStatus.ERROR
        
        if isinstance(last_error, asyncio.TimeoutError):
            raise MCPTimeoutError(
                self.server_id,
                f"Connection timed out after {timeout}s",
                retry_count
            )
        
        raise MCPConnectionError(
            self.server_id,
            str(last_error) if last_error else "Unknown error",
            retry_count
        )
    
    async def _establish_connection(self, timeout: float) -> None:
        """Internal method to establish the actual connection.
        
        Args:
            timeout: Connection timeout in seconds.
        """
        server_params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=self.config.env if self.config.env else None,
        )
        
        # Create the stdio client context manager
        self._context_manager = stdio_client(server_params)
        
        # Enter the context manager with timeout
        async with asyncio.timeout(timeout):
            self._read_stream, self._write_stream = await self._context_manager.__aenter__()
            
            # Create and initialize the session
            self.session = ClientSession(self._read_stream, self._write_stream)
            await self.session.__aenter__()
            await self.session.initialize()
    
    async def disconnect(self) -> None:
        """Disconnect from the MCP server.
        
        Cleans up the session and connection resources.
        """
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception as e:
                # Suppress cancel scope errors - known issue with anyio/MCP SDK
                if "cancel scope" not in str(e).lower():
                    logger.warning(f"Error closing session for '{self.server_id}': {e}")
            self.session = None
        
        if self._context_manager:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except RuntimeError as e:
                # Suppress "cancel scope in different task" errors - known MCP SDK issue
                if "cancel scope" not in str(e).lower():
                    logger.warning(f"Error closing connection for '{self.server_id}': {e}")
            except Exception as e:
                logger.warning(
                    f"Error closing connection for '{self.server_id}': {e}"
                )
            self._context_manager = None
            self._read_stream = None
            self._write_stream = None
        
        self.tools = []
        self.status = ServerStatus.DISCONNECTED
        logger.info(f"Disconnected from MCP server '{self.server_id}'")
    
    async def discover_tools(self) -> List[MCPTool]:
        """Discover available tools from the server.
        
        Returns:
            List of MCPTool objects representing available tools.
            
        Raises:
            MCPConnectionError: If not connected to the server.
        """
        if self.status != ServerStatus.CONNECTED or not self.session:
            raise MCPConnectionError(
                self.server_id,
                "Not connected to server"
            )
        
        try:
            result = await self.session.list_tools()
            
            self.tools = [
                MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                    server_id=self.server_id,
                )
                for tool in result.tools
            ]
            
            logger.info(
                f"Discovered {len(self.tools)} tools from '{self.server_id}': "
                f"{[t.name for t in self.tools]}"
            )
            return self.tools
            
        except Exception as e:
            logger.error(f"Error discovering tools from '{self.server_id}': {e}")
            raise MCPConnectionError(
                self.server_id,
                f"Failed to discover tools: {e}"
            )
    
    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: Optional[float] = None
    ) -> MCPToolResult:
        """Call a tool on the MCP server.
        
        Args:
            tool_name: Name of the tool to call.
            arguments: Dictionary of tool arguments.
            timeout: Call timeout in seconds. Uses config default if None.
            
        Returns:
            MCPToolResult containing the result or error.
        """
        if self.status != ServerStatus.CONNECTED or not self.session:
            return MCPToolResult(
                success=False,
                content=None,
                error=f"Not connected to server '{self.server_id}'"
            )
        
        if timeout is None:
            timeout = self.config.timeout
        
        start_time = time.time()
        
        try:
            async with asyncio.timeout(timeout):
                result = await self.session.call_tool(tool_name, arguments)
            
            execution_time = time.time() - start_time
            
            # Extract content from result
            content = None
            if result.content:
                if len(result.content) == 1:
                    content = result.content[0].text if hasattr(result.content[0], 'text') else result.content[0]
                else:
                    content = [
                        item.text if hasattr(item, 'text') else item
                        for item in result.content
                    ]
            
            return MCPToolResult(
                success=not result.isError if hasattr(result, 'isError') else True,
                content=content,
                error=None,
                execution_time=execution_time
            )
            
        except asyncio.TimeoutError:
            execution_time = time.time() - start_time
            error_msg = f"Tool call timed out after {timeout}s"
            logger.error(f"[{self.server_id}] {error_msg}")
            return MCPToolResult(
                success=False,
                content=None,
                error=error_msg,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = str(e)
            logger.error(f"[{self.server_id}] Tool call error: {error_msg}")
            return MCPToolResult(
                success=False,
                content=None,
                error=error_msg,
                execution_time=execution_time
            )
