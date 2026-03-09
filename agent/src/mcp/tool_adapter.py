"""MCP Tool Adapter for LangChain integration.

This module provides adapters to convert MCP tools into LangChain
compatible tools for use with the ReAct agent.
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional, Type, TYPE_CHECKING

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model

if TYPE_CHECKING:
    from .client_manager import MCPClientManager

logger = logging.getLogger(__name__)


def create_input_model(
    tool_name: str,
    input_schema: Dict[str, Any]
) -> Type[BaseModel]:
    """Create a Pydantic model from a JSON Schema.
    
    Args:
        tool_name: Name of the tool (used for model naming).
        input_schema: JSON Schema defining the input parameters.
        
    Returns:
        A dynamically created Pydantic model class.
    """
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    
    fields = {}
    for prop_name, prop_schema in properties.items():
        prop_type = _json_type_to_python(prop_schema.get("type", "string"))
        description = prop_schema.get("description", "")
        default = ... if prop_name in required else None
        
        fields[prop_name] = (
            Optional[prop_type] if prop_name not in required else prop_type,
            Field(default=default, description=description)
        )
    
    # Create a unique model name
    model_name = f"{tool_name.replace('-', '_').title()}Input"
    
    if not fields:
        # Create an empty model if no properties
        # Note: Pydantic doesn't allow field names starting with underscore
        fields["placeholder_field"] = (Optional[str], Field(default=None))
    
    return create_model(model_name, **fields)


def _json_type_to_python(json_type: str) -> type:
    """Convert JSON Schema type to Python type.
    
    Args:
        json_type: JSON Schema type string.
        
    Returns:
        Corresponding Python type.
    """
    type_mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return type_mapping.get(json_type, str)


class MCPToolAdapter(BaseTool):
    """Adapter to convert MCP tools to LangChain tools.
    
    Wraps an MCP tool to make it compatible with LangChain's
    tool interface for use with agents.
    
    Attributes:
        name: The tool name.
        description: Human-readable description.
        mcp_client_manager: Reference to the MCP client manager.
        original_tool_name: The original MCP tool name.
        input_schema_dict: The tool's input JSON Schema.
    """
    
    name: str = ""
    description: str = ""
    original_tool_name: str = ""
    input_schema_dict: Dict[str, Any] = Field(default_factory=dict)
    
    # Store reference to client manager (not serialized)
    _mcp_client_manager: Optional["MCPClientManager"] = None
    
    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
    
    def __init__(
        self,
        name: str,
        description: str,
        mcp_client_manager: "MCPClientManager",
        original_tool_name: str,
        input_schema: Dict[str, Any],
        **kwargs
    ):
        """Initialize the tool adapter.
        
        Args:
            name: The tool name (may include server prefix).
            description: Human-readable description.
            mcp_client_manager: Reference to the MCP client manager.
            original_tool_name: The original MCP tool name.
            input_schema: JSON Schema for tool inputs.
        """
        # Create args_schema from input_schema
        args_schema = create_input_model(name, input_schema)
        
        super().__init__(
            name=name,
            description=description,
            original_tool_name=original_tool_name,
            input_schema_dict=input_schema,
            args_schema=args_schema,
            **kwargs
        )
        self._mcp_client_manager = mcp_client_manager
    
    def _run(self, **kwargs) -> str:
        """Synchronous tool execution.
        
        Runs the async implementation in an event loop.
        
        Args:
            **kwargs: Tool arguments.
            
        Returns:
            JSON string of the tool result.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, create a new task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self._arun(**kwargs)
                    )
                    return future.result()
            else:
                return loop.run_until_complete(self._arun(**kwargs))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(self._arun(**kwargs))
    
    async def _arun(self, **kwargs) -> str:
        """Asynchronous tool execution.
        
        Args:
            **kwargs: Tool arguments.
            
        Returns:
            JSON string of the tool result.
        """
        if self._mcp_client_manager is None:
            return json.dumps({
                "error": "MCP client manager not initialized"
            })
        
        # Remove placeholder field if present
        kwargs.pop("placeholder_field", None)
        
        # Serialize arguments
        try:
            serialized_args = self._serialize_arguments(kwargs)
        except Exception as e:
            logger.error(f"Argument serialization error: {e}")
            return json.dumps({
                "error": f"Invalid arguments: {e}"
            })
        
        # Call the tool
        result = await self._mcp_client_manager.call_tool(
            self.name,
            serialized_args
        )
        
        # Format the response
        return self._format_response(result)
    
    def _serialize_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize arguments to match the tool's input schema.
        
        Args:
            arguments: Raw argument dictionary.
            
        Returns:
            Serialized arguments ready for the MCP call.
            
        Raises:
            ValueError: If arguments don't match the schema.
        """
        schema_props = self.input_schema_dict.get("properties", {})
        required = set(self.input_schema_dict.get("required", []))
        
        serialized = {}
        
        for key, value in arguments.items():
            if key not in schema_props:
                continue  # Skip unknown arguments
            
            prop_schema = schema_props[key]
            expected_type = prop_schema.get("type", "string")
            
            # Convert value to expected type
            serialized[key] = self._convert_value(value, expected_type)
        
        # Check required fields
        missing = required - set(serialized.keys())
        if missing:
            raise ValueError(f"Missing required arguments: {missing}")
        
        return serialized
    
    def _convert_value(self, value: Any, expected_type: str) -> Any:
        """Convert a value to the expected JSON Schema type.
        
        Args:
            value: The value to convert.
            expected_type: The expected JSON Schema type.
            
        Returns:
            The converted value.
        """
        if value is None:
            return None
        
        if expected_type == "string":
            return str(value)
        elif expected_type == "integer":
            return int(value)
        elif expected_type == "number":
            return float(value)
        elif expected_type == "boolean":
            return bool(value)
        elif expected_type == "array":
            return list(value) if not isinstance(value, list) else value
        elif expected_type == "object":
            return dict(value) if not isinstance(value, dict) else value
        
        return value
    
    def _format_response(self, result) -> str:
        """Format the MCP tool result as a JSON string.
        
        Args:
            result: MCPToolResult object.
            
        Returns:
            JSON string representation of the result.
        """
        from .models import MCPToolResult
        
        if isinstance(result, MCPToolResult):
            if result.success:
                # Try to return content directly if it's already a string
                if isinstance(result.content, str):
                    return result.content
                return json.dumps(result.content, ensure_ascii=False, indent=2)
            else:
                return json.dumps({
                    "error": result.error,
                    "execution_time": result.execution_time
                })
        
        # Fallback for unexpected result types
        return json.dumps(result, ensure_ascii=False, default=str)


def create_mcp_tools(
    mcp_client_manager: "MCPClientManager"
) -> list[MCPToolAdapter]:
    """Create LangChain tools from all available MCP tools.
    
    Args:
        mcp_client_manager: The MCP client manager with connected servers.
        
    Returns:
        List of MCPToolAdapter instances.
    """
    tools = []
    
    for tool in mcp_client_manager.get_available_tools():
        adapter = MCPToolAdapter(
            name=tool.name,
            description=tool.description,
            mcp_client_manager=mcp_client_manager,
            original_tool_name=tool.name,
            input_schema=tool.input_schema,
        )
        tools.append(adapter)
    
    return tools
