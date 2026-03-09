"""Tool Registry for managing native and MCP tools.

This module provides a centralized registry for all tools available
to the ReAct agent, including both native tools and MCP tools.
"""
import logging
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from langchain_core.tools import BaseTool

if TYPE_CHECKING:
    from ..mcp.tool_adapter import MCPToolAdapter
    from ..mcp.models import MCPTool
    from ..mcp.client_manager import MCPClientManager

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Centralized registry for all agent tools.
    
    Manages both native LangChain tools and MCP tools, providing
    a unified interface for tool registration, lookup, and listing.
    
    Handles tool name collisions by prefixing MCP tool names with
    their server identifier when conflicts occur.
    
    Attributes:
        native_tools: Dictionary of native LangChain tools.
        mcp_tools: Dictionary of MCP tool adapters.
        _tool_to_server: Mapping from tool names to server IDs.
        _server_tools: Mapping from server IDs to their tool names.
    """
    
    def __init__(self):
        """Initialize an empty tool registry."""
        self.native_tools: Dict[str, BaseTool] = {}
        self.mcp_tools: Dict[str, "MCPToolAdapter"] = {}
        self._tool_to_server: Dict[str, str] = {}
        self._server_tools: Dict[str, Set[str]] = {}
    
    def register_native_tool(self, tool: BaseTool) -> None:
        """Register a native LangChain tool.
        
        Args:
            tool: The LangChain tool to register.
        """
        if tool.name in self.native_tools:
            logger.warning(f"Overwriting existing native tool: {tool.name}")
        
        self.native_tools[tool.name] = tool
        logger.info(f"Registered native tool: {tool.name}")
    
    def register_native_tools(self, tools: List[BaseTool]) -> None:
        """Register multiple native LangChain tools.
        
        Args:
            tools: List of LangChain tools to register.
        """
        for tool in tools:
            self.register_native_tool(tool)
    
    def unregister_native_tool(self, tool_name: str) -> bool:
        """Unregister a native tool by name.
        
        Args:
            tool_name: Name of the tool to unregister.
            
        Returns:
            True if the tool was found and removed, False otherwise.
        """
        if tool_name in self.native_tools:
            del self.native_tools[tool_name]
            logger.info(f"Unregistered native tool: {tool_name}")
            return True
        return False

    def register_mcp_tools(
        self,
        tools: List["MCPToolAdapter"],
        server_id: str
    ) -> List[str]:
        """Register MCP tools from a server.
        
        Registers all tools from an MCP server, handling name collisions
        by prefixing with the server identifier when necessary.
        
        Args:
            tools: List of MCPToolAdapter instances to register.
            server_id: Identifier of the MCP server providing these tools.
            
        Returns:
            List of registered tool names (may include prefixed names).
        """
        registered_names = []
        
        if server_id not in self._server_tools:
            self._server_tools[server_id] = set()
        
        for tool in tools:
            registered_name = self._register_single_mcp_tool(tool, server_id)
            registered_names.append(registered_name)
        
        logger.info(
            f"Registered {len(registered_names)} MCP tools from server '{server_id}'"
        )
        return registered_names
    
    def _register_single_mcp_tool(
        self,
        tool: "MCPToolAdapter",
        server_id: str
    ) -> str:
        """Register a single MCP tool, handling name collisions.
        
        Args:
            tool: The MCPToolAdapter to register.
            server_id: Identifier of the MCP server.
            
        Returns:
            The actual name under which the tool was registered.
        """
        original_name = tool.name
        registered_name = original_name
        
        # Check for collision with native tools
        if original_name in self.native_tools:
            registered_name = f"{server_id}_{original_name}"
            logger.warning(
                f"Tool name '{original_name}' conflicts with native tool, "
                f"registering as '{registered_name}'"
            )
        # Check for collision with existing MCP tools from different servers
        elif original_name in self.mcp_tools:
            existing_server = self._tool_to_server.get(original_name)
            if existing_server and existing_server != server_id:
                registered_name = f"{server_id}_{original_name}"
                logger.warning(
                    f"Tool name '{original_name}' conflicts with tool from "
                    f"server '{existing_server}', registering as '{registered_name}'"
                )
        
        # Update tool name if it was changed
        if registered_name != original_name:
            # Create a new adapter with the prefixed name
            tool.name = registered_name
        
        self.mcp_tools[registered_name] = tool
        self._tool_to_server[registered_name] = server_id
        self._server_tools[server_id].add(registered_name)
        
        logger.debug(f"Registered MCP tool: {registered_name} from server {server_id}")
        return registered_name
    
    def unregister_mcp_tools(self, server_id: str) -> List[str]:
        """Unregister all MCP tools from a specific server.
        
        Removes all tools associated with the given server ID from
        the registry.
        
        Args:
            server_id: Identifier of the MCP server whose tools to remove.
            
        Returns:
            List of tool names that were unregistered.
        """
        if server_id not in self._server_tools:
            logger.warning(f"No tools registered for server '{server_id}'")
            return []
        
        tools_to_remove = list(self._server_tools[server_id])
        
        for tool_name in tools_to_remove:
            if tool_name in self.mcp_tools:
                del self.mcp_tools[tool_name]
            if tool_name in self._tool_to_server:
                del self._tool_to_server[tool_name]
        
        del self._server_tools[server_id]
        
        logger.info(
            f"Unregistered {len(tools_to_remove)} tools from server '{server_id}'"
        )
        return tools_to_remove

    def get_all_tools(self) -> List[BaseTool]:
        """Get all registered tools (native + MCP).
        
        Returns:
            Combined list of all native and MCP tools.
        """
        all_tools: List[BaseTool] = []
        all_tools.extend(self.native_tools.values())
        all_tools.extend(self.mcp_tools.values())
        return all_tools
    
    def get_native_tools(self) -> List[BaseTool]:
        """Get all native tools.
        
        Returns:
            List of native LangChain tools.
        """
        return list(self.native_tools.values())
    
    def get_mcp_tools(self) -> List["MCPToolAdapter"]:
        """Get all MCP tools.
        
        Returns:
            List of MCP tool adapters.
        """
        return list(self.mcp_tools.values())
    
    def get_tool(self, tool_name: str) -> Optional[BaseTool]:
        """Get a specific tool by name.
        
        Searches both native and MCP tools.
        
        Args:
            tool_name: Name of the tool to retrieve.
            
        Returns:
            The tool if found, None otherwise.
        """
        if tool_name in self.native_tools:
            return self.native_tools[tool_name]
        if tool_name in self.mcp_tools:
            return self.mcp_tools[tool_name]
        return None
    
    def get_server_tools(self, server_id: str) -> List["MCPToolAdapter"]:
        """Get all tools from a specific MCP server.
        
        Args:
            server_id: Identifier of the MCP server.
            
        Returns:
            List of tools from that server.
        """
        if server_id not in self._server_tools:
            return []
        
        return [
            self.mcp_tools[name]
            for name in self._server_tools[server_id]
            if name in self.mcp_tools
        ]
    
    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is registered.
        
        Args:
            tool_name: Name of the tool to check.
            
        Returns:
            True if the tool exists, False otherwise.
        """
        return tool_name in self.native_tools or tool_name in self.mcp_tools
    
    def get_tool_count(self) -> int:
        """Get total number of registered tools.
        
        Returns:
            Total count of native and MCP tools.
        """
        return len(self.native_tools) + len(self.mcp_tools)
    
    def get_server_ids(self) -> List[str]:
        """Get list of server IDs with registered tools.
        
        Returns:
            List of MCP server identifiers.
        """
        return list(self._server_tools.keys())
    
    def clear(self) -> None:
        """Clear all registered tools."""
        self.native_tools.clear()
        self.mcp_tools.clear()
        self._tool_to_server.clear()
        self._server_tools.clear()
        logger.info("Cleared all tools from registry")
    
    def get_tool_descriptions(self) -> str:
        """Get formatted descriptions of all tools.
        
        Returns:
            Formatted string with all tool names and descriptions.
        """
        descriptions = []
        
        for tool in self.get_all_tools():
            desc = f"- {tool.name}: {tool.description}"
            descriptions.append(desc)
        
        return "\n".join(descriptions)


# Global registry instance
_global_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry instance.
    
    Creates a new registry if one doesn't exist.
    
    Returns:
        The global ToolRegistry instance.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def reset_tool_registry() -> None:
    """Reset the global tool registry.
    
    Creates a new empty registry, discarding the old one.
    """
    global _global_registry
    _global_registry = ToolRegistry()
    logger.info("Reset global tool registry")
