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
    user_id: str,
    apply_id: str,
    group_id: str,
    memory_config: MemoryConfig,
    messages: list,
) -> dict:
    """
    Write structured messages to the memory system.
    
    Args:
        ctx: FastMCP context for dependency injection
        user_id: User identifier
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        messages: Structured message list [{"role": "user", "content": "..."}, ...]
        
    Returns:
        dict: Contains 'status', 'saved_to', and 'data' fields
        
    Raises:
        ValueError: If messages is empty or invalid format
    """
    try:
        if not messages or not isinstance(messages, list) or len(messages) == 0:
            raise ValueError("messages parameter must be a non-empty list")
        
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise ValueError(f"Message {idx} must be a dictionary")
            if 'role' not in msg or 'content' not in msg:
                raise ValueError(f"Message {idx} must contain 'role' and 'content' fields")
            if msg['role'] not in ['user', 'assistant']:
                raise ValueError(f"Message {idx} role must be 'user' or 'assistant', got: {msg['role']}")
        
        os.makedirs("data_output", exist_ok=True)
        file_path = os.path.join("data_output", "user_data.csv")

        logger.info(f"Writing {len(messages)} structured messages to memory system")
        
        await write(
            messages=messages,
            user_id=user_id,
            apply_id=apply_id,
            group_id=group_id,
            memory_config=memory_config,
        )
        
        formatted_content = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
        
        logger.info(f"Write completed successfully! Config: {memory_config.config_name}")

        return {
            "status": "success",
            "saved_to": file_path,
            "data": formatted_content,
            "message_count": len(messages),
            "config_id": memory_config.config_id,
            "config_name": memory_config.config_name,
        }

    except Exception as e:
        logger.error(f"Data_write failed: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
        }
