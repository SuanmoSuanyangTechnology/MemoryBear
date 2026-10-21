"""MCP tool module - Model Context Protocol support."""

# Main class exports
from .base import MCPTool, MCPToolManager, MCPError
from .client import SimpleMCPClient, MCPConnectionError

__all__ = [
    # Core classes
    "MCPTool",
    "MCPToolManager",
    "MCPError",

    # Client classes
    "SimpleMCPClient",
    "MCPConnectionError",
]
