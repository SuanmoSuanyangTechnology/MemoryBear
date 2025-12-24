"""
Problem Tools for question segmentation and extension.

This module contains MCP tools for breaking down and extending user questions.
LLM clients are constructed from MemoryConfig when needed.
"""

import json
import time

from app.core.logging_config import get_agent_logger, log_time
from app.core.memory.agent.mcp_server.mcp_instance import mcp
from app.core.memory.agent.mcp_server.models.problem_models import (
    ProblemBreakdownResponse,
    ProblemExtensionResponse,
)
from app.core.memory.agent.mcp_server.server import get_context_resource
from app.core.memory.agent.utils.messages_tool import Problem_Extension_messages_deal
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.schemas.memory_config_schema import MemoryConfig
from mcp.server.fastmcp import Context

logger = get_agent_logger(__name__)


@mcp.tool()
async def Split_The_Problem(
    ctx: Context,
    sentence: str,
    sessionid: str,
    messages_id: str,
    apply_id: str,
    group_id: str,
    memory_config: MemoryConfig,
) -> dict:
    """
    Segment the dialogue or sentence into sub-problems.
    
    Args:
        ctx: FastMCP context for dependency injection
        sentence: Original sentence to split
        sessionid: Session identifier
        messages_id: Message identifier
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        
    Returns:
        dict: Contains 'context' (JSON string of split results) and 'original' sentence
    """
    start = time.time()

    try:
        # Extract services from context
        template_service = get_context_resource(ctx, "template_service")
        session_service = get_context_resource(ctx, "session_service")

        # Get LLM client from memory_config
        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client_from_config(memory_config)
        
        # Extract user ID from session
        user_id = session_service.resolve_user_id(sessionid)
        
        # Get conversation history
        history = await session_service.get_history(user_id, apply_id, group_id)
        # Override with empty list for now (as in original)
        history = []
        
        # Render template
        try:
            system_prompt = await template_service.render_template(
                template_name='problem_breakdown_prompt.jinja2',
                operation_name='split_the_problem',
                history=history,
                sentence=sentence
            )
        except Exception as e:
            logger.error(
                f"Template rendering failed for Split_The_Problem: {e}",
                exc_info=True
            )
            return {
                "context": json.dumps([], ensure_ascii=False),
                "original": sentence,
                "error": f"Prompt rendering failed: {str(e)}"
            }
        
        # Call LLM with structured response
        try:
            structured = await llm_client.response_structured(
                messages=[{"role": "system", "content": system_prompt}],
                response_model=ProblemBreakdownResponse
            )
            
            # Handle RootModel response with .root attribute access
            if structured is None:
                # LLM returned None, use empty list as fallback
                split_result = json.dumps([], ensure_ascii=False)
            elif hasattr(structured, 'root') and structured.root is not None:
                split_result = json.dumps(
                    [item.model_dump() for item in structured.root],
                    ensure_ascii=False
                )
            elif isinstance(structured, list):
                # Fallback: treat structured itself as the list
                split_result = json.dumps(
                    [item.model_dump() for item in structured],
                    ensure_ascii=False
                )
            else:
                # Last resort: use empty list
                split_result = json.dumps([], ensure_ascii=False)
                
        except Exception as e:
            logger.error(
                f"LLM call failed for Split_The_Problem: {e}",
                exc_info=True
            )
            split_result = json.dumps([], ensure_ascii=False)
        
        logger.info("Problem splitting")
        logger.info(f"Problem split result: {split_result}")
        
        # Emit intermediate output for frontend
        result = {
            "context": split_result,
            "original": sentence,
            "_intermediate": {
                "type": "problem_split",
                "data": json.loads(split_result) if split_result else [],
                "original_query": sentence
            }
        }
        
        return result
        
    except Exception as e:
        logger.error(
            f"Split_The_Problem failed: {e}",
            exc_info=True
        )
        return {
            "context": json.dumps([], ensure_ascii=False),
            "original": sentence,
            "error": str(e)
        }
        
    finally:
        # Log execution time
        end = time.time()
        try:
            duration = end - start
        except Exception:
            duration = 0.0
        log_time('Problem splitting', duration)


@mcp.tool()
async def Problem_Extension(
    ctx: Context,
    context: dict,
    usermessages: str,
    apply_id: str,
    group_id: str,
    memory_config: MemoryConfig,
    storage_type: str = "",
    user_rag_memory_id: str = "",
) -> dict:
    """
    Extend the problem with additional sub-questions.
    
    Args:
        ctx: FastMCP context for dependency injection
        context: Dictionary containing split problem results
        usermessages: User messages identifier
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        storage_type: Storage type for the workspace (optional)
        user_rag_memory_id: User RAG memory identifier (optional)
        
    Returns:
        dict: Contains 'context' (aggregated questions) and 'original' question
    """
    start = time.time()

    try:
        # Extract services from context
        template_service = get_context_resource(ctx, "template_service")
        session_service = get_context_resource(ctx, "session_service")

        # Get LLM client from memory_config
        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client_from_config(memory_config)
        
        # Resolve session ID from usermessages
        from app.core.memory.agent.utils.messages_tool import Resolve_username
        sessionid = Resolve_username(usermessages)
        
        # Get conversation history
        history = await session_service.get_history(sessionid, apply_id, group_id)
        # Override with empty list for now (as in original)
        history = []
        
        # Process context to extract questions
        extent_quest, original = await Problem_Extension_messages_deal(context)
        
        # Format questions for template rendering
        questions_formatted = []
        for msg in extent_quest:
            if msg.get("role") == "user":
                questions_formatted.append(msg.get("content", ""))
        
        # Render template
        try:
            system_prompt = await template_service.render_template(
                template_name='Problem_Extension_prompt.jinja2',
                operation_name='problem_extension',
                history=history,
                questions=questions_formatted
            )
        except Exception as e:
            logger.error(
                f"Template rendering failed for Problem_Extension: {e}",
                exc_info=True
            )
            return {
                "context": {},
                "original": original,
                "error": f"Prompt rendering failed: {str(e)}"
            }
        
        # Call LLM with structured response
        try:
            response_content = await llm_client.response_structured(
                messages=[{"role": "system", "content": system_prompt}],
                response_model=ProblemExtensionResponse
            )
            
            # Aggregate results by original question
            aggregated_dict = {}
            for item in response_content.root:
                key = getattr(item, "original_question", None) or (
                    item.get("original_question") if isinstance(item, dict) else None
                )
                value = getattr(item, "extended_question", None) or (
                    item.get("extended_question") if isinstance(item, dict) else None
                )
                if not key or not value:
                    continue
                aggregated_dict.setdefault(key, []).append(value)
                
        except Exception as e:
            logger.error(
                f"LLM call failed for Problem_Extension: {e}",
                exc_info=True
            )
            aggregated_dict = {}
        
        logger.info("Problem extension")
        logger.info(f"Problem extension result: {aggregated_dict}")
        
        # Emit intermediate output for frontend
        result = {
            "context": aggregated_dict,
            "original": original,
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "_intermediate": {
                "type": "problem_extension",
                "data": aggregated_dict,
                "original_query": original,
                "storage_type": storage_type,
                "user_rag_memory_id": user_rag_memory_id
            }
        }
        
        return result
        
    except Exception as e:
        logger.error(
            f"Problem_Extension failed: {e}",
            exc_info=True
        )
        return {
            "context": {},
            "original": context.get("original", ""),
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "error": str(e)
        }
        
    finally:
        # Log execution time
        end = time.time()
        try:
            duration = end - start
        except Exception:
            duration = 0.0
        log_time('Problem extension', duration)
