"""
Data Tools for data type differentiation and writing.

This module contains MCP tools for distinguishing data types and writing data.
"""
import os

from mcp.server.fastmcp import Context

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.mcp_server.mcp_instance import mcp
from app.core.memory.agent.mcp_server.server import get_context_resource
from app.core.memory.agent.mcp_server.models.retrieval_models import DistinguishTypeResponse
from app.core.memory.agent.utils.write_tools import write


logger = get_agent_logger(__name__)


@mcp.tool()
async def Data_type_differentiation(
    ctx: Context,
    context: str
) -> dict:
    """
    Distinguish the type of data (read or write).
    
    Args:
        ctx: FastMCP context for dependency injection
        context: Text to analyze for type differentiation
        
    Returns:
        dict: Contains 'context' with the original text and 'type' field
    """
    try:
        # Extract services from context
        template_service = get_context_resource(ctx, 'template_service')
        llm_client = get_context_resource(ctx, 'llm_client')
        
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
    config_id: str
) -> dict:
    """
    Write data to the database/file system.
    
    Args:
        ctx: FastMCP context for dependency injection
        content: Data content to write
        user_id: User identifier
        apply_id: Application identifier
        group_id: Group identifier
        config_id: Configuration ID for processing (optional, integer)
        
    Returns:
        dict: Contains 'status', 'saved_to', and 'data' fields
    """
    try:
        # Ensure output directory exists
        os.makedirs("data_output", exist_ok=True)
        file_path = os.path.join("data_output", "user_data.csv")
        
        # Write data using utility function
        try:
            await write(content, user_id, apply_id, group_id, config_id=config_id)
            logger.info(f"写入成功！Config ID: {config_id if config_id else 'None'}")
            
            return {
                "status": "success",
                "saved_to": file_path,
                "data": content,
                "config_id": config_id
            }
            
        except Exception as e:
            logger.error(f"写入失败: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e)
            }
            
    except Exception as e:
        logger.error(
            f"Data_write failed: {e}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": str(e)
        }
