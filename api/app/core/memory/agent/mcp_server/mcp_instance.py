"""
MCP Server Instance

This module contains the FastMCP server instance that is shared across all modules.
It's in a separate file to avoid circular import issues.
"""
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server instance
# This instance is shared across all tool modules
mcp = FastMCP('data_flow')
