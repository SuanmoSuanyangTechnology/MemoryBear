import os
import time

from app.core.logging_config import get_agent_logger, log_time
from app.core.memory.agent.models.summary_models import (
    RetrieveSummaryResponse,
    SummaryResponse,
)
from app.core.memory.agent.services.optimized_llm_service import LLMServiceMixin
from app.core.memory.agent.services.search_service import SearchService
from app.core.memory.agent.utils.llm_tools import (
    PROJECT_ROOT_,
    ReadState,
)
from app.core.memory.agent.utils.redis_tool import store
from app.core.memory.agent.utils.session_tools import SessionService
from app.core.memory.agent.utils.template_tools import TemplateService
from app.core.rag.nlp.search import knowledge_retrieval
from app.db import get_db_context

template_root = os.path.join(PROJECT_ROOT_, 'memory', 'agent', 'utils', 'prompt')
logger = get_agent_logger(__name__)


class SummaryNodeService(LLMServiceMixin):
    """总结节点服务类"""

    def __init__(self):
        super().__init__()
        self.template_service = TemplateService(template_root)


# 创建全局服务实例
summary_service = SummaryNodeService()


async def rag_config(state):
    user_rag_memory_id = state.get('user_rag_memory_id', '')
    kb_config = {
        "knowledge_bases": [
            {
                "kb_id": user_rag_memory_id,
                "similarity_threshold": 0.7,
                "vector_similarity_weight": 0.5,
                "top_k": 10,
                "retrieve_type": "participle"
            }
        ],
        "merge_strategy": "weight",
        "reranker_id": os.getenv('reranker_id'),
        "reranker_top_k": 10
    }
    return kb_config


async def rag_knowledge(state, question):
    kb_config = await rag_config(state)
    end_user_id = state.get('end_user_id', '')
    user_rag_memory_id = state.get("user_rag_memory_id", '')
    retrieve_chunks_result = knowledge_retrieval(question, kb_config, [str(end_user_id)])
    try:
        retrieval_knowledge = [i.page_content for i in retrieve_chunks_result]
        clean_content = '\n\n'.join(retrieval_knowledge)
        cleaned_query = question
        raw_results = clean_content
        logger.info(f" Using RAG storage with memory_id={user_rag_memory_id}")
    except  Exception:
        retrieval_knowledge = []
        clean_content = ''
        raw_results = ''
        cleaned_query = question
        logger.info(f"No content retrieved from knowledge base: {user_rag_memory_id}")
    return retrieval_knowledge, clean_content, cleaned_query, raw_results


async def summary_history(state: ReadState) -> ReadState:
    end_user_id = state.get("end_user_id", '')
    history = await SessionService(store).get_history(end_user_id, end_user_id, end_user_id)
    return history


async def summary_llm(state: ReadState, history, retrieve_info, template_name, operation_name, response_model,
                      search_mode) -> str:
    """
    增强的summary_llm函数，包含更好的错误处理和数据验证
    """
    data = state.get("data", '')

    # 构建系统提示词
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
        # 使用优化的LLM服务进行结构化输出
        with get_db_context() as db_session:
            structured = await summary_service.call_llm_structured(
                state=state,
                db_session=db_session,
                system_prompt=system_prompt,
                response_model=response_model,
                fallback_value=None
            )
        # 验证结构化响应
        if structured is None:
            logger.warning("LLM返回None，使用默认回答")
            return "信息不足，无法回答"

        # 根据操作类型提取答案
        if operation_name == "summary":
            aimessages = getattr(structured, 'query_answer', None) or "信息不足，无法回答"
        else:
            # 处理RetrieveSummaryResponse
            if hasattr(structured, 'data') and structured.data:
                aimessages = getattr(structured.data, 'query_answer', None) or "信息不足，无法回答"
            else:
                logger.warning("结构化响应缺少data字段")
                aimessages = "信息不足，无法回答"

        # 验证答案不为空
        if not aimessages or aimessages.strip() == "":
            aimessages = "信息不足，无法回答"

        return aimessages

    except Exception as e:
        logger.error(f"结构化输出失败: {e}", exc_info=True)

        # 尝试非结构化输出作为fallback
        try:
            logger.info("尝试非结构化输出作为fallback")
            response = await summary_service.call_llm_simple(
                state=state,
                db_session=db_session,
                system_prompt=system_prompt,
                fallback_message="信息不足，无法回答"
            )

            if response and response.strip():
                # 简单清理响应
                cleaned_response = response.strip()
                # 移除可能的JSON标记
                if cleaned_response.startswith('```'):
                    lines = cleaned_response.split('\n')
                    cleaned_response = '\n'.join(lines[1:-1])

                return cleaned_response
            else:
                return "信息不足，无法回答"

        except Exception as fallback_error:
            logger.error(f"Fallback也失败: {fallback_error}")
            return "信息不足，无法回答"


async def summary_redis_save(state: ReadState, aimessages) -> ReadState:
    data = state.get("data", '')
    end_user_id = state.get("end_user_id", '')
    await SessionService(store).save_session(
        user_id=end_user_id,
        query=data,
        apply_id=end_user_id,
        end_user_id=end_user_id,
        ai_response=aimessages
    )
    await SessionService(store).cleanup_duplicates()
    logger.info(f"sessionid: {aimessages} 写入成功")


async def summary_prompt(state: ReadState, aimessages, raw_results) -> ReadState:
    storage_type = state.get("storage_type", '')
    user_rag_memory_id = state.get("user_rag_memory_id", '')
    data = state.get("data", '')
    input_summary = {
        "status": "success",
        "summary_result": aimessages,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id,
        "_intermediate": {
            "type": "input_summary",
            "title": "快速答案",
            "summary": aimessages,
            "query": data,
            "raw_results": raw_results,
            "search_mode": "quick_search",
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
    }
    retrieve = {
        "status": "success",
        "summary_result": aimessages,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id,
        "_intermediate": {
            "type": "retrieval_summary",
            "title": "快速检索",
            "summary": aimessages,
            "query": data,
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
    }

    return input_summary, retrieve


async def Input_Summary(state: ReadState) -> ReadState:
    start = time.time()
    storage_type = state.get("storage_type", '')
    memory_config = state.get('memory_config', None)
    user_rag_memory_id = state.get("user_rag_memory_id", '')
    data = state.get("data", '')
    end_user_id = state.get("end_user_id", '')
    logger.info(f"Input_Summary: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")
    history = await summary_history(state)
    search_params = {
        "end_user_id": end_user_id,
        "question": data,
        "return_raw_results": True,
        "include": ["summaries"]  # Only search summary nodes for faster performance
    }

    try:
        if storage_type != "rag":
            retrieve_info, question, raw_results = await SearchService().execute_hybrid_search(**search_params,
                                                                                               memory_config=memory_config)
        else:
            retrieval_knowledge, retrieve_info, question, raw_results = await rag_knowledge(state, data)
    except Exception as e:
        logger.error(f"Input_Summary: hybrid_search failed, using empty results: {e}", exc_info=True)
        retrieve_info, question, raw_results = "", data, []
    try:
        # aimessages=await summary_llm(state,history,retrieve_info,'Retrieve_Summary_prompt.jinja2',
        #                              'input_summary',RetrieveSummaryResponse)
        # logger.info(f"快速答案总结==>>:{storage_type}--{user_rag_memory_id}--{aimessages}")
        summary_result = await summary_prompt(state, retrieve_info, retrieve_info)
        summary = summary_result[0]
    except Exception as e:
        logger.error(f"Input_Summary failed: {e}", exc_info=True)
        summary = {
            "status": "fail",
            "summary_result": "信息不足，无法回答",
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "error": str(e)
        }
    end = time.time()
    try:
        duration = end - start
    except Exception:
        duration = 0.0
    log_time('检索', duration)
    return {"summary": summary}


async def Retrieve_Summary(state: ReadState) -> ReadState:
    retrieve = state.get("retrieve", '')
    history = await summary_history(state)
    import json
    with open("检索.json", "w", encoding='utf-8') as f:
        f.write(json.dumps(retrieve, indent=4, ensure_ascii=False))
    retrieve = retrieve.get("Expansion_issue", [])
    start = time.time()
    retrieve_info_str = []
    for data in retrieve:
        if data == '':
            retrieve_info_str = ''
        else:
            for key, value in data.items():
                if key == 'Answer_Small':
                    for i in value:
                        retrieve_info_str.append(i)
    retrieve_info_str = list(set(retrieve_info_str))
    retrieve_info_str = '\n'.join(retrieve_info_str)

    aimessages = await  summary_llm(state, history, retrieve_info_str,
                                    'direct_summary_prompt.jinja2', 'retrieve_summary', RetrieveSummaryResponse, "1")
    if '信息不足，无法回答' not in str(aimessages) or str(aimessages) != "":
        await summary_redis_save(state, aimessages)
    if aimessages == '':
        aimessages = '信息不足，无法回答'
    logger.info(f"Summary after retrieval: {aimessages}")
    end = time.time()
    try:
        duration = end - start
    except Exception:
        duration = 0.0
    log_time('Retrieval summary', duration)

    # 修复协程调用 - 先await，然后访问返回值
    summary_result = await summary_prompt(state, aimessages, retrieve_info_str)
    summary = summary_result[1]
    return {"summary": summary}


async def Summary(state: ReadState) -> ReadState:
    start = time.time()
    query = state.get("data", '')
    verify = state.get("verify", '')
    verify_expansion_issue = verify.get("verified_data", '')
    retrieve_info_str = ''
    for data in verify_expansion_issue:
        for key, value in data.items():
            if key == 'answer_small':
                for i in value:
                    retrieve_info_str += i + '\n'
    history = await summary_history(state)

    data = {
        "query": query,
        "history": history,
        "retrieve_info": retrieve_info_str
    }
    aimessages = await  summary_llm(state, history, data,
                                    'summary_prompt.jinja2', 'summary', SummaryResponse, 0)

    if '信息不足，无法回答' not in str(aimessages) or str(aimessages) != "":
        await summary_redis_save(state, aimessages)
    if aimessages == '':
        aimessages = '信息不足，无法回答'
    try:
        duration = time.time() - start
    except Exception:
        duration = 0.0
    log_time('Retrieval summary', duration)

    # 修复协程调用 - 先await，然后访问返回值
    summary_result = await summary_prompt(state, aimessages, retrieve_info_str)
    summary = summary_result[1]
    return {"summary": summary}


async def Summary_fails(state: ReadState) -> ReadState:
    storage_type = state.get("storage_type", '')
    user_rag_memory_id = state.get("user_rag_memory_id", '')
    history = await summary_history(state)
    query = state.get("data", '')
    verify = state.get("verify", '')
    verify_expansion_issue = verify.get("verified_data", '')
    retrieve_info_str = ''
    for data in verify_expansion_issue:
        for key, value in data.items():
            if key == 'answer_small':
                for i in value:
                    retrieve_info_str += i + '\n'
    data = {
        "query": query,
        "history": history,
        "retrieve_info": retrieve_info_str
    }
    aimessages = await summary_llm(state, history, data,
                                   'fail_summary_prompt.jinja2', 'summary', SummaryResponse, 0)
    result = {
        "status": "success",
        "summary_result": aimessages,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id
    }
    return {"summary": result}
