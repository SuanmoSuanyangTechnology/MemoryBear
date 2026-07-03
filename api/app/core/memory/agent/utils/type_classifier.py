"""
Type classification utility for distinguishing read/write operations.
"""
from app.core.logging_config import get_agent_logger, log_prompt_rendering
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.messages_tools import read_template_file
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.db import get_db_context
from jinja2 import Template
from pydantic import BaseModel

logger = get_agent_logger(__name__)


class DistinguishTypeResponse(BaseModel):
    """Response model for type classification"""
    type: str


async def status_typle(messages: str, llm_model_id: str, tenant_id=None) -> dict:
    """
    Classify message type as read or write operation.
    Updated to eliminate global variables in favor of explicit parameters.
    
    Args:
        messages: User message to classify
        llm_model_id: LLM model ID to use (required, no longer from global variables)
        tenant_id: Tenant ID for SpeedBear public model authentication
        
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
        llm_client = ModelClientMixin.get_llm_client(db, llm_model_id, tenant_id=tenant_id)

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
