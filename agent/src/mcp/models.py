"""Data models for MCP integration.

This module defines the core data structures used throughout the MCP
client implementation.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ServerStatus(Enum):
    """MCP server connection status."""
    
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class MCPTool:
    """MCP tool definition.
    
    Represents a tool discovered from an MCP server.
    
    Attributes:
        name: The tool's unique name.
        description: Human-readable description of what the tool does.
        input_schema: JSON Schema defining the tool's input parameters.
        server_id: Identifier of the MCP server providing this tool.
    """
    
    name: str
    description: str
    input_schema: Dict[str, Any]
    server_id: str


@dataclass
class MCPToolResult:
    """Result of an MCP tool call.
    
    Attributes:
        success: Whether the tool call succeeded.
        content: The result content (type depends on the tool).
        error: Error message if the call failed.
        execution_time: Time taken to execute the tool in seconds.
    """
    
    success: bool
    content: Any
    error: Optional[str] = None
    execution_time: float = 0.0


@dataclass
class ArxivPaper:
    """arXiv paper data model.
    
    Represents metadata for an arXiv paper.
    
    Attributes:
        arxiv_id: The arXiv identifier (e.g., "2301.00001").
        title: Paper title.
        authors: List of author names.
        abstract: Paper abstract text.
        categories: List of arXiv categories.
        published: Publication date string.
        updated: Last update date string (optional).
        pdf_url: URL to the PDF file (optional).
        local_path: Local filesystem path if downloaded (optional).
    """
    
    arxiv_id: str
    title: str
    authors: List[str]
    abstract: str
    categories: List[str]
    published: str
    updated: Optional[str] = None
    pdf_url: Optional[str] = None
    local_path: Optional[str] = None
    
    @property
    def abs_url(self) -> str:
        """Get the arXiv abstract page URL."""
        return f"https://arxiv.org/abs/{self.arxiv_id}"
    
    @property
    def default_pdf_url(self) -> str:
        """Get the default arXiv PDF URL."""
        return f"https://arxiv.org/pdf/{self.arxiv_id}.pdf"
