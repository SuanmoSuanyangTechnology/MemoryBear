"""
Data Tools for data type differentiation and writing.

This module contains MCP tools for distinguishing data types and writing data.
"""

import os

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.mcp_server.mcp_instance import mcp
from app.core.memory.agent.mcp_server.models.retrieval_models import (
    DistinguishTypeResponse,
)
from app.core.memory.agent.mcp_server.server import get_context_resource
from app.core.memory.agent.utils.write_tools import write
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.schemas.memory_config_schema import MemoryConfig
from mcp.server.fastmcp import Context

logger = get_agent_logger(__name__)


@mcp.tool()
async def Data_type_differentiation(
    ctx: Context,
    context: str,
    memory_config: MemoryConfig,
) -> dict:
    """
    Distinguish the type of data (read or write).
    
    Args:
        ctx: FastMCP context for dependency injection
        context: Text to analyze for type differentiation
        memory_config: MemoryConfig object containing LLM configuration
        
    Returns:
        dict: Contains 'context' with the original text and 'type' field
    """
    try:
        # Extract services from context
        template_service = get_context_resource(ctx, 'template_service')
        
        # Get LLM client from memory_config using factory pattern
        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client_from_config(memory_config)
        
        # Render template
        try:
            system_prompt = await template_service.render_template(
                template_name='distinguish_types_prompt.jinja2',
                operation_name='status_typle',
                user_query=context
            )
        except Exception as e:
            logger.error(
                f"Template rendering failed for Data_type_differentiation: {e}",
                exc_info=True
            )
            return {
                "type": "error",
                "message": f"Prompt rendering failed: {str(e)}"
            }

        # Call LLM with structured response
        try:
            structured = await llm_client.response_structured(
                messages=[{"role": "system", "content": system_prompt}],
                response_model=DistinguishTypeResponse
            )
            
            result = structured.model_dump()
            
            # Add context to result
            result["context"] = context
            
            return result
            
        except Exception as e:
            logger.error(
                f"LLM call failed for Data_type_differentiation: {e}",
                exc_info=True
            )
            return {
                "context": context,
                "type": "error",
                "message": f"LLM call failed: {str(e)}"
            }
            
    except Exception as e:
        logger.error(
            f"Data_type_differentiation failed: {e}",
            exc_info=True
        )
        return {
            "context": context,
            "type": "error",
            "message": str(e)
        }


@mcp.tool()
async def Data_write(
    ctx: Context,
    content: str,
    user_id: str,
    apply_id: str,
    group_id: str,
    memory_config: MemoryConfig,
) -> dict:
    """
    Write data to the database/file system.
    
    Args:
        ctx: FastMCP context for dependency injection
        content: Data content to write
        user_id: User identifier
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        
    Returns:
        dict: Contains 'status', 'saved_to', and 'data' fields
    """
    try:
        # Ensure output directory exists
        os.makedirs("data_output", exist_ok=True)
        file_path = os.path.join("data_output", "user_data.csv")

        # Write data - clients are constructed inside write() from memory_config
        await write(
            content=content,
            user_id=user_id,
            apply_id=apply_id,
            group_id=group_id,
            memory_config=memory_config,
        )
        logger.info(f"Write completed successfully! Config: {memory_config.config_name}")

        return {
            "status": "success",
            "saved_to": file_path,
            "data": content,
            "config_id": memory_config.config_id,
            "config_name": memory_config.config_name,
        }

    except Exception as e:
        logger.error(f"Data_write failed: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }
