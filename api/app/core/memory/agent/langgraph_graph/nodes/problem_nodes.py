import json
import os
import time

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.models.problem_models import ProblemExtensionResponse
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


class ProblemNodeService(LLMServiceMixin):
    """
    Problem processing node service class
    
    Handles problem decomposition and extension operations using LLM services.
    Inherits from LLMServiceMixin to provide structured LLM calling capabilities.
    
    Attributes:
        template_service: Service for rendering Jinja2 templates
    """

    def __init__(self):
        super().__init__()
        self.template_service = TemplateService(template_root)


# Create global service instance
problem_service = ProblemNodeService()


async def Split_The_Problem(state: ReadState) -> ReadState:
    """
    Problem decomposition node
    
    Breaks down complex user queries into smaller, more manageable sub-problems.
    Uses LLM to analyze the input and generate structured problem decomposition
    with question types and reasoning.
    
    Args:
        state: ReadState containing user input and configuration
        
    Returns:
        ReadState: Updated state with problem decomposition results
    """
    # 从状态中获取数据
    content = state.get('data', '')
    end_user_id = state.get('end_user_id', '')
    memory_config = state.get('memory_config', None)

    history = await SessionService(store).get_history(end_user_id, end_user_id, end_user_id)

    # 生成 JSON schema 以指导 LLM 输出正确格式
    json_schema = ProblemExtensionResponse.model_json_schema()

    system_prompt = await problem_service.template_service.render_template(
        template_name='problem_breakdown_prompt.jinja2',
        operation_name='split_the_problem',
        history=history,
        sentence=content,
        json_schema=json_schema
    )

    try:
        # 使用优化的LLM服务
        with get_db_context() as db_session:
            structured = await problem_service.call_llm_structured(
                state=state,
                db_session=db_session,
                system_prompt=system_prompt,
                response_model=ProblemExtensionResponse,
                fallback_value=[]
            )

        # 添加更详细的日志记录
        logger.info(f"Split_The_Problem: 开始处理问题分解，内容长度: {len(content)}")

        # Validate structured response
        if not structured or not hasattr(structured, 'root'):
            logger.warning("Split_The_Problem: 结构化响应为空或格式不正确")
            split_result = json.dumps([], ensure_ascii=False)
        elif not structured.root:
            logger.warning("Split_The_Problem: 结构化响应的root为空")
            split_result = json.dumps([], ensure_ascii=False)
        else:
            split_result = json.dumps(
                [item.model_dump() for item in structured.root],
                ensure_ascii=False
            )

        split_result_dict = []
        for index, item in enumerate(json.loads(split_result)):
            split_data = {
                "id": f"Q{index + 1}",
                "question": item['extended_question'],
                "type": item['type'],
                "reason": item['reason']
            }
            split_result_dict.append(split_data)

        logger.info(f"Split_The_Problem: 成功生成 {len(structured.root) if structured.root else 0} 个分解项")

        result = {
            "context": split_result,
            "original": content,
            "_intermediate": {
                "type": "problem_split",
                "title": "问题拆分",
                "data": split_result_dict,
                "original_query": content
            }
        }

    except Exception as e:
        logger.error(
            f"Split_The_Problem failed: {e}",
            exc_info=True
        )

        # Provide more detailed error information
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "content_length": len(content),
            "llm_model_id": str(memory_config.llm_model_id) if memory_config else None
        }

        logger.error(f"Split_The_Problem error details: {error_details}")

        # Create default empty result
        result = {
            "context": json.dumps([], ensure_ascii=False),
            "original": content,
            "error": str(e),
            "_intermediate": {
                "type": "problem_split",
                "title": "问题拆分",
                "data": [],
                "original_query": content,
                "error": error_details
            }
        }

    # Return updated state including spit_context field
    return {"spit_data": result}


async def Problem_Extension(state: ReadState) -> ReadState:
    """
    Problem extension node
    
    Extends the decomposed problems from Split_The_Problem node by generating
    additional related questions and organizing them by original question.
    Uses LLM to create comprehensive question extensions for better memory retrieval.
    
    Args:
        state: ReadState containing decomposed problems and configuration
        
    Returns:
        ReadState: Updated state with extended problem results
    """
    # Get original data and decomposition results
    start = time.time()
    content = state.get('data', '')
    data = state.get('spit_data', '')['context']
    end_user_id = state.get('end_user_id', '')
    storage_type = state.get('storage_type', '')
    user_rag_memory_id = state.get('user_rag_memory_id', '')
    memory_config = state.get('memory_config', None)

    databasets = {}
    try:
        data = json.loads(data)
        for i in data:
            databasets[i['extended_question']] = i['type']
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"Problem_Extension: 数据解析失败: {e}")
        # 使用空字典作为fallback
        databasets = {}
        data = []

    history = await SessionService(store).get_history(end_user_id, end_user_id, end_user_id)

    # 生成 JSON schema 以指导 LLM 输出正确格式
    json_schema = ProblemExtensionResponse.model_json_schema()

    system_prompt = await problem_service.template_service.render_template(
        template_name='Problem_Extension_prompt.jinja2',
        operation_name='problem_extension',
        history=history,
        questions=databasets,
        json_schema=json_schema
    )

    try:
        # 使用优化的LLM服务
        with get_db_context() as db_session:
            response_content = await problem_service.call_llm_structured(
                state=state,
                db_session=db_session,
                system_prompt=system_prompt,
                response_model=ProblemExtensionResponse,
                fallback_value=[]
            )

        logger.info(f"Problem_Extension: 开始处理问题扩展，问题数量: {len(databasets)}")

        # Validate structured response
        if not response_content or not hasattr(response_content, 'root'):
            logger.warning("Problem_Extension: 结构化响应为空或格式不正确")
            aggregated_dict = {}
        elif not response_content.root:
            logger.warning("Problem_Extension: 结构化响应的root为空")
            aggregated_dict = {}
        else:
            # Aggregate results by original question
            aggregated_dict = {}
            for item in response_content.root:
                try:
                    key = getattr(item, "original_question", None) or (
                        item.get("original_question") if isinstance(item, dict) else None
                    )
                    value = getattr(item, "extended_question", None) or (
                        item.get("extended_question") if isinstance(item, dict) else None
                    )
                    if not key or not value:
                        logger.warning(f"Problem_Extension: 跳过无效项: key={key}, value={value}")
                        continue
                    aggregated_dict.setdefault(key, []).append(value)
                except Exception as item_error:
                    logger.warning(f"Problem_Extension: 处理项目时出错: {item_error}")
                    continue

        logger.info(f"Problem_Extension: 成功生成 {len(aggregated_dict)} 个扩展问题组")

    except Exception as e:
        logger.error(
            f"LLM call failed for Problem_Extension: {e}",
            exc_info=True
        )

        # Provide more detailed error information
        error_details = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "questions_count": len(databasets),
            "llm_model_id": str(memory_config.llm_model_id) if memory_config else None
        }

        logger.error(f"Problem_Extension error details: {error_details}")
        aggregated_dict = {}

    logger.info("Problem extension")
    logger.info(f"Problem extension result: {aggregated_dict}")

    # Emit intermediate output for frontend
    result = {
        "context": aggregated_dict,
        "original": data,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id,
        "_intermediate": {
            "type": "problem_extension",
            "title": "问题扩展",
            "data": aggregated_dict,
            "original_query": content,
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
    }

    return {"problem_extension": result}