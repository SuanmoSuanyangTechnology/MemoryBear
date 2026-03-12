import asyncio
import os

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.models.verification_models import VerificationResult
from app.core.memory.agent.services.optimized_llm_service import LLMServiceMixin
from app.core.memory.agent.utils.llm_tools import (
    PROJECT_ROOT_,
    ReadState,
)
from app.core.memory.agent.utils.redis_tool import store
from app.core.memory.agent.utils.session_tools import SessionService
from app.core.memory.agent.utils.template_tools import TemplateService
from app.db import get_db_context

template_root = os.path.join(PROJECT_ROOT_, 'memory', 'agent', 'utils', 'prompt')
logger = get_agent_logger(__name__)


class VerificationNodeService(LLMServiceMixin):
    """
    Verification node service class
    
    Handles data verification operations using LLM services. Inherits from 
    LLMServiceMixin to provide structured LLM calling capabilities for 
    verifying and validating retrieved information.
    
    Attributes:
        template_service: Service for rendering Jinja2 templates
    """

    def __init__(self):
        super().__init__()
        self.template_service = TemplateService(template_root)


# Create global service instance
verification_service = VerificationNodeService()


async def Verify_prompt(state: ReadState, messages_deal: VerificationResult):
    """
    Process verification results and generate output format
    
    Transforms VerificationResult objects into structured output format suitable
    for frontend consumption. Handles conversion of VerificationItem objects to
    dictionary format and adds metadata for tracking.
    
    Args:
        state: ReadState containing storage and user configuration
        messages_deal: VerificationResult containing verification outcomes
        
    Returns:
        dict: Formatted verification result with status and metadata
    """
    storage_type = state.get('storage_type', '')
    user_rag_memory_id = state.get('user_rag_memory_id', '')
    data = state.get('data', '')

    # Convert VerificationItem objects to dictionary list
    verified_data = []
    if messages_deal.expansion_issue:
        for item in messages_deal.expansion_issue:
            if hasattr(item, 'model_dump'):
                verified_data.append(item.model_dump())
            elif isinstance(item, dict):
                verified_data.append(item)

    Verify_result = {
        "status": messages_deal.split_result,
        "verified_data": verified_data,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id,
        "_intermediate": {
            "type": "verification",
            "title": "Data Verification",
            "result": messages_deal.split_result,
            "reason": messages_deal.reason or "验证完成",
            "query": messages_deal.query,
            "verified_count": len(verified_data),
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
    }
    return Verify_result


async def Verify(state: ReadState):
    logger.info("=== Verify 节点开始执行 ===")
    try:
        content = state.get('data', '')
        end_user_id = state.get('end_user_id', '')
        memory_config = state.get('memory_config', None)

        logger.info(f"Verify: content={content[:50] if content else 'empty'}..., end_user_id={end_user_id}")

        history = await SessionService(store).get_history(end_user_id, end_user_id, end_user_id)
        logger.info(f"Verify: 获取历史记录完成，history length={len(history)}")

        retrieve = state.get("retrieve", {})
        logger.info(
            f"Verify: retrieve data type={type(retrieve)}, keys={retrieve.keys() if isinstance(retrieve, dict) else 'N/A'}")

        retrieve_expansion = retrieve.get("Expansion_issue", []) if isinstance(retrieve, dict) else []
        logger.info(f"Verify: Expansion_issue length={len(retrieve_expansion)}")

        messages = {
            "Query": content,
            "Expansion_issue": retrieve_expansion
        }

        logger.info("Verify: 开始渲染模板")

        # Generate JSON schema to guide LLM output format
        json_schema = VerificationResult.model_json_schema()

        system_prompt = await verification_service.template_service.render_template(
            template_name='split_verify_prompt.jinja2',
            operation_name='split_verify_prompt',
            history=history,
            sentence=messages,
            json_schema=json_schema
        )
        logger.info(f"Verify: 模板渲染完成，prompt length={len(system_prompt)}")

        # 使用优化的LLM服务，添加超时保护
        logger.info("Verify: 开始调用 LLM")
        try:
            # Add asyncio.wait_for timeout wrapper to prevent infinite waiting
            # Timeout set to 150 seconds (slightly longer than LLM config's 120 seconds)

            with get_db_context() as db_session:
                structured = await asyncio.wait_for(
                    verification_service.call_llm_structured(
                        state=state,
                        db_session=db_session,
                        system_prompt=system_prompt,
                        response_model=VerificationResult,
                        fallback_value={
                            "query": content,
                            "history": history if isinstance(history, list) else [],
                            "expansion_issue": [],
                            "split_result": "failed",
                            "reason": "验证失败或超时"
                        }
                    ),
                    timeout=150.0  # 150 second timeout
                )
            logger.info(f"Verify: LLM 调用完成，result={structured}")
        except asyncio.TimeoutError:
            logger.error("Verify: LLM 调用超时（150秒），使用 fallback 值")
            structured = VerificationResult(
                query=content,
                history=history if isinstance(history, list) else [],
                expansion_issue=[],
                split_result="failed",
                reason="LLM调用超时"
            )

        result = await Verify_prompt(state, structured)
        logger.info("=== Verify 节点执行完成 ===")
        return {"verify": result}

    except Exception as e:
        logger.error(f"Verify 节点执行失败: {e}", exc_info=True)
        # 返回失败的验证结果
        return {
            "verify": {
                "status": "failed",
                "verified_data": [],
                "storage_type": state.get('storage_type', ''),
                "user_rag_memory_id": state.get('user_rag_memory_id', ''),
                "_intermediate": {
                    "type": "verification",
                    "title": "Data Verification",
                    "result": "failed",
                    "reason": f"验证过程出错: {str(e)}",
                    "query": state.get('data', ''),
                    "verified_count": 0,
                    "storage_type": state.get('storage_type', ''),
                    "user_rag_memory_id": state.get('user_rag_memory_id', '')
                }
            }
        }
