"""
Type classification utility for distinguishing read/write operations.
"""
from app.core.config import settings
from app.core.logging_config import get_agent_logger, log_prompt_rendering
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.messages_tool import read_template_file
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from jinja2 import Template
from pydantic import BaseModel

logger = get_agent_logger(__name__)


class DistinguishTypeResponse(BaseModel):
    """Response model for type classification"""
    type: str


async def status_typle(messages: str, llm_model_id: str) -> dict:
    """
    Classify message type as read or write operation.
    Updated to eliminate global variables in favor of explicit parameters.
    
    Args:
        messages: User message to classify
        llm_model_id: LLM model ID to use (required, no longer from global variables)
        
    Returns:
        dict: Contains 'type' field with classification result
    """
    try:
        file_path = PROJECT_ROOT_ + '/agent/utils/prompt/distinguish_types_prompt.jinja2'
        template_content = await read_template_file(file_path)
        template = Template(template_content)
        system_prompt = template.render(user_query=messages)
        log_prompt_rendering("status_typle", system_prompt)
    except Exception as e:
        logger.error(f"Template rendering failed for status_typle: {e}", exc_info=True)
        return {
            "type": "error",
            "message": f"Prompt rendering failed: {str(e)}"
        }
    
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        llm_client = factory.get_llm_client(llm_model_id)

    try:
        structured = await llm_client.response_structured(
            messages=[{"role": "system", "content": system_prompt}],
            response_model=DistinguishTypeResponse
        )
        return structured.model_dump()
    except Exception as e:
        logger.error(f"LLM call failed for status_typle: {e}", exc_info=True)
        return {
            "type": "error",
            "message": f"LLM call failed: {str(e)}"
        }
