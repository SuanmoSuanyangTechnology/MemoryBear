"""
Verification Tools for data verification.

This module contains MCP tools for verifying retrieved data.
"""
import time

from jinja2 import Template
from mcp.server.fastmcp import Context

from app.core.logging_config import get_agent_logger, log_time
from app.core.memory.agent.mcp_server.mcp_instance import mcp
from app.core.memory.agent.mcp_server.server import get_context_resource
from app.core.memory.agent.utils.verify_tool import VerifyTool
from app.core.memory.agent.utils.messages_tool import (
    Verify_messages_deal,
    Retrieve_verify_tool_messages_deal,
    Resolve_username
)
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_


logger = get_agent_logger(__name__)


@mcp.tool()
async def Verify(
    ctx: Context,
    context: dict,
    usermessages: str,
    apply_id: str,
    group_id: str,
    storage_type: str = "",
    user_rag_memory_id: str = ""
) -> dict:
    """
    Verify the retrieved data.
    
    Args:
        ctx: FastMCP context for dependency injection
        context: Dictionary containing query and expansion issues
        usermessages: User messages identifier
        apply_id: Application identifier
        group_id: Group identifier
        storage_type: Storage type for the workspace (optional)
        user_rag_memory_id: User RAG memory identifier (optional)
        
    Returns:
        dict: Contains 'status' and 'verified_data' with verification results
    """
    start = time.time()


    try:
        # Extract services from context
        session_service = get_context_resource(ctx, 'session_service')
        
        # Load verification prompt template
        file_path = PROJECT_ROOT_ + '/agent/utils/prompt/split_verify_prompt.jinja2'
        
        # Read template file directly (VerifyTool expects raw template content)
        from app.core.memory.agent.utils.messages_tool import read_template_file
        system_prompt = await read_template_file(file_path)


        
        # Resolve session ID
        sessionid = Resolve_username(usermessages)
        
        # Get conversation history
        history = await session_service.get_history(sessionid, apply_id, group_id)

        template = Template(system_prompt)
        system_prompt = template.render(history=history, sentence=context)
        
        # Process context to extract query and results
        Query_small, Result_small, query = await Verify_messages_deal(context)
        
        # Build query list for verification
        query_list = []
        for query_small, anser in zip(Query_small, Result_small, strict=False):
            query_list.append({
                'Query_small': query_small,
                'Answer_Small': anser
            })
        
        messages = {
            "Query": query,
            "Expansion_issue": query_list
        }


        
        # Call verification workflow
        verify_tool = VerifyTool(system_prompt, messages)
        verify_result = await verify_tool.verify()
        
        # Parse LLM verification result with error handling
        try:
            messages_deal = await Retrieve_verify_tool_messages_deal(
                verify_result,
                history,
                query
            )
        except Exception as e:
            logger.error(
                f"Retrieve_verify_tool_messages_deal parsing failed: {e}",
                exc_info=True
            )
            # Fallback to avoid 500 errors
            messages_deal = {
                "data": {
                    "query": query,
                    "expansion_issue": []
                },
                "split_result": "failed",
                "reason": str(e),
                "history": history,
            }
        
        logger.info(f"验证==>>:{messages_deal}")
        
        # Emit intermediate output for frontend
        return {
            "status": "success",
            "verified_data": messages_deal,
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "_intermediate": {
                "type": "verification",
                "title": "数据验证",
                "result": messages_deal.get("split_result", "unknown"),
                "reason": messages_deal.get("reason", ""),
                "query": query,
                "verified_count": len(query_list),
                "storage_type": storage_type,
                "user_rag_memory_id": user_rag_memory_id
            }
        }
        
    except Exception as e:
        logger.error(
            f"Verify failed: {e}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": str(e),
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "verified_data": {
                "data": {
                    "query": "",
                    "expansion_issue": []
                },
                "split_result": "failed",
                "reason": str(e),
                "history": [],
            }
        }
        
    finally:
        # Log execution time
        end = time.time()
        try:
            duration = end - start
        except Exception:
            duration = 0.0
        log_time('验证', duration)
