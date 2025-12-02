"""
Type classification utility for distinguishing read/write operations.
"""
from jinja2 import Template
from pydantic import BaseModel

from app.core.logging_config import get_agent_logger, log_prompt_rendering
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.messages_tool import read_template_file
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.config import settings


logger = get_agent_logger(__name__)


class DistinguishTypeResponse(BaseModel):
    """Response model for type classification"""
    type: str


async def status_typle(messages: str) -> dict:
    """
    Classify message type as read or write operation.
    
    Args:
        messages: User message to classify
        
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
    
    from app.core.memory.utils.config import definitions as config_defs
    llm_client = get_llm_client(config_defs.SELECTED_LLM_ID)

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
