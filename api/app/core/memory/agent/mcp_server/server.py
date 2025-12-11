"""
MCP Server initialization with FastMCP context setup.

This module initializes the FastMCP server and registers shared resources
in the context for dependency injection into tool functions.
"""
import os
import sys
from mcp.server.fastmcp import FastMCP

from app.core.config import settings
from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.redis_tool import RedisSessionStore, store
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.utils.config.definitions import SELECTED_LLM_ID,reload_configuration_from_database
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.agent.mcp_server.services.template_service import TemplateService
from app.core.memory.agent.mcp_server.services.search_service import SearchService
from app.core.memory.agent.mcp_server.services.session_service import SessionService
from app.core.memory.agent.mcp_server.mcp_instance import mcp


logger = get_agent_logger(__name__)


def get_context_resource(ctx, resource_name: str):
    """
    Helper function to retrieve a resource from the FastMCP context.
    
    Args:
        ctx: FastMCP Context object (passed to tool functions)
        resource_name: Name of the resource to retrieve
        
    Returns:
        The requested resource
        
    Raises:
        AttributeError: If the resource doesn't exist
        
    Example:
        @mcp.tool()
        async def my_tool(ctx: Context):
            template_service = get_context_resource(ctx, 'template_service')
            llm_client = get_context_resource(ctx, 'llm_client')
    """
    if not hasattr(ctx, 'fastmcp') or ctx.fastmcp is None:
        raise RuntimeError("Context does not have fastmcp attribute")
    
    if not hasattr(ctx.fastmcp, resource_name):
        raise AttributeError(
            f"Resource '{resource_name}' not found in context. "
            f"Available resources: {[k for k in dir(ctx.fastmcp) if not k.startswith('_')]}"
        )
    
    return getattr(ctx.fastmcp, resource_name)


def initialize_context():
    """
    Initialize and register shared resources in FastMCP context.
    
    This function sets up all shared resources that will be available
    to tool functions via dependency injection through the context parameter.
    
    Resources are stored as attributes on the FastMCP instance and can be
    accessed via ctx.fastmcp in tool functions.
    
    Resources registered:
    - session_store: RedisSessionStore for session management
    - llm_client: LLM client for structured API calls
    - app_settings: Application settings (renamed to avoid conflict with FastMCP settings)
    - template_service: Service for template rendering
    - search_service: Service for hybrid search
    - session_service: Service for session operations
    """
    try:
        # Register Redis session store
        logger.info("Registering session_store in context")
        mcp.session_store = store
        
        # Register LLM client
        try:
            logger.info(f"Registering llm_client in context with model ID: {SELECTED_LLM_ID}")
            llm_client = get_llm_client(SELECTED_LLM_ID)
            mcp.llm_client = llm_client
            logger.info("llm_client registered successfully")
        except Exception as e:
            logger.error(f"Failed to register llm_client: {e}", exc_info=True)
            # 注册一个 None 值，避免工具调用时找不到资源
            mcp.llm_client = None
            logger.warning("llm_client set to None due to initialization failure")
        
        # Register application settings (renamed to avoid conflict with FastMCP's settings)
        logger.info("Registering app_settings in context")
        mcp.app_settings = settings
        
        # Register template service
        template_root = PROJECT_ROOT_ + '/agent/utils/prompt'
        # logger.info(f"Registering template_service in context with root: {template_root}")
        template_service = TemplateService(template_root)
        mcp.template_service = template_service
        
        # Register search service
        # logger.info("Registering search_service in context")
        search_service = SearchService()
        mcp.search_service = search_service
        
        # Register session service
        # logger.info("Registering session_service in context")
        session_service = SessionService(store)
        mcp.session_service = session_service
        
        # logger.info("All context resources registered successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize context: {e}", exc_info=True)
        raise


def main():
    """
    Main entry point for the MCP server.
    
    Initializes context and starts the server with SSE transport.
    """
    try:
        # logger.info("Starting MCP server initialization")
        reload_configuration_from_database(config_id=os.getenv("config_id"), force_reload=True)
        # Initialize context resources
        initialize_context()
        
        # Import and register tools
        # logger.info("Importing MCP tools")
        from app.core.memory.agent.mcp_server.tools import (
            problem_tools,
            retrieval_tools,
            verification_tools,
            summary_tools,
            data_tools
        )
        # logger.info("All MCP tools imported and registered")
        
        # Log registered tools for debugging
        import asyncio
        tools_list = asyncio.run(mcp.list_tools())
        # logger.info(f"Registered {len(tools_list)} MCP tools: {[t.name for t in tools_list]}")
        
        # Get MCP port from environment (default: 8081)
        mcp_port = int(os.getenv("MCP_PORT", "8081"))
        # logger.info(f"Starting MCP server on {settings.SERVER_IP}:{mcp_port} with SSE transport")
        
        # Run the server with SSE transport for HTTP connections
        import uvicorn
        app = mcp.sse_app()
        uvicorn.run(app, host=settings.SERVER_IP, port=mcp_port, log_level="info")
        
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
