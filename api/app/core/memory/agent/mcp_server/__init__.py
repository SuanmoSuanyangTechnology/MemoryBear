"""
MCP Server package for memory agent.

This package provides the FastMCP server implementation with context-based
dependency injection for tool functions.

Package structure:
- server: FastMCP server initialization and context setup
- tools: MCP tool implementations
- models: Pydantic response models
- services: Business logic services
"""
# from app.core.memory.agent.mcp_server.server import (
#     mcp,
#     initialize_context,
#     main,
#     get_context_resource
# )

# # Import tools to register them (but don't export them)
# from app.core.memory.agent.mcp_server import tools

# __all__ = [
#     'mcp',
#     'initialize_context',
#     'main',
#     'get_context_resource',
# ]