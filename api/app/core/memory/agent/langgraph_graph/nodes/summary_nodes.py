import os

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.services.optimized_llm_service import LLMServiceMixin
from app.core.memory.agent.utils.llm_tools import (
    PROJECT_ROOT_,
    ReadState,
)
from app.core.memory.agent.utils.template_tools import TemplateService
from app.db import get_db_context

template_root = os.path.join(PROJECT_ROOT_, 'memory', 'agent', 'utils', 'prompt')
logger = get_agent_logger(__name__)


class SummaryNodeService(LLMServiceMixin):
    """
    Summary node service class
    
    Handles summary generation operations using LLM services. Inherits from 
    LLMServiceMixin to provide structured LLM calling capabilities for 
    generating summaries from retrieved information.
    
    Attributes:
        template_service: Service for rendering Jinja2 templates
    """

    def __init__(self):
        super().__init__()
        self.template_service = TemplateService(template_root)


# Create global service instance
summary_service = SummaryNodeService()


async def summary_llm(state: ReadState, history, retrieve_info, template_name, operation_name, response_model,
                      search_mode) -> str:
    """
    Enhanced summary_llm function with better error handling and data validation
    
    Generates summaries using LLM with structured output. Includes fallback mechanisms
    for handling LLM failures and provides robust error recovery.
    
    Args:
        state: ReadState containing current context
        history: Conversation history for context
        retrieve_info: Retrieved information to summarize
        template_name: Jinja2 template name for prompt generation
        operation_name: Type of operation (summary, input_summary, retrieve_summary)
        response_model: Pydantic model for structured output
        search_mode: Search mode flag ("0" for simple, "1" for complex)
        
    Returns:
        str: Generated summary text or fallback message
    """
    data = state.get("data", '')

    # Build system prompt
    if str(search_mode) == "0":
        system_prompt = await summary_service.template_service.render_template(
            template_name=template_name,
            operation_name=operation_name,
            data=retrieve_info,
            query=data
        )
    else:
        system_prompt = await summary_service.template_service.render_template(
            template_name=template_name,
            operation_name=operation_name,
            query=data,
            history=history,
            retrieve_info=retrieve_info
        )
    try:
        # Use optimized LLM service for structured output
        with get_db_context() as db_session:
            structured = await summary_service.call_llm_structured(
                state=state,
                db_session=db_session,
                system_prompt=system_prompt,
                response_model=response_model,
                fallback_value=None
            )
        # Validate structured response
        if structured is None:
            logger.warning("LLM返回None，使用默认回答")
            return "信息不足，无法回答"

        # Extract answer based on operation type
        if operation_name == "summary":
            aimessages = getattr(structured, 'query_answer', None) or "信息不足，无法回答"
        else:
            # Handle RetrieveSummaryResponse
            if hasattr(structured, 'data') and structured.data:
                aimessages = getattr(structured.data, 'query_answer', None) or "信息不足，无法回答"
            else:
                logger.warning("结构化响应缺少data字段")
                aimessages = "信息不足，无法回答"

        # Validate answer is not empty
        if not aimessages or aimessages.strip() == "":
            aimessages = "信息不足，无法回答"

        return aimessages

    except Exception as e:
        logger.error(f"结构化输出失败: {e}", exc_info=True)

        # Try unstructured output as fallback
        try:
            logger.info("尝试非结构化输出作为fallback")
            response = await summary_service.call_llm_simple(
                state=state,
                db_session=db_session,
                system_prompt=system_prompt,
                fallback_message="信息不足，无法回答"
            )

            if response and response.strip():
                # Simple response cleaning
                cleaned_response = response.strip()
                # Remove possible JSON markers
                if cleaned_response.startswith('```'):
                    lines = cleaned_response.split('\n')
                    cleaned_response = '\n'.join(lines[1:-1])

                return cleaned_response
            else:
                return "信息不足，无法回答"

        except Exception as fallback_error:
            logger.error(f"Fallback也失败: {fallback_error}")
            return "信息不足，无法回答"
