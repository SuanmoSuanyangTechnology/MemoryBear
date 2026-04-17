import asyncio
import os
import time

from app.core.logging_config import get_agent_logger, log_time
from app.core.memory.agent.langgraph_graph.nodes.perceptual_retrieve_node import (
    PerceptualSearchService,
)
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


async def rag_config(state):
    """
    Configure RAG (Retrieval-Augmented Generation) settings for summary operations
    
    Creates configuration for knowledge base retrieval including similarity thresholds,
    weights, and reranker settings specifically for summary generation.
    
    Args:
        state: Current state containing user_rag_memory_id
        
    Returns:
        dict: RAG configuration dictionary with knowledge base settings
    """
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
    """
    Retrieve knowledge using RAG approach for summary generation
    
    Performs knowledge retrieval from configured knowledge bases using the
    provided question and returns formatted results for summary processing.
    
    Args:
        state: Current state containing configuration
        question: Question to search for in knowledge base
        
    Returns:
        tuple: (retrieval_knowledge, clean_content, cleaned_query, raw_results)
            - retrieval_knowledge: List of retrieved knowledge chunks
            - clean_content: Formatted content string
            - cleaned_query: Processed query string
            - raw_results: Raw retrieval results
    """
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
    """
    Retrieve conversation history for summary context
    
    Gets the conversation history for the current user to provide context
    for summary generation operations.
    
    Args:
        state: ReadState containing end_user_id
        
    Returns:
        ReadState: Conversation history data
    """
    end_user_id = state.get("end_user_id", '')
    history = await SessionService(store).get_history(end_user_id, end_user_id, end_user_id)
    return history


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


async def summary_redis_save(state: ReadState, aimessages) -> ReadState:
    """
    Save summary results to Redis session storage
    
    Stores the generated summary and user query in Redis for session management
    and conversation history tracking.
    
    Args:
        state: ReadState containing user and query information
        aimessages: Generated summary message to save
        
    Returns:
        ReadState: Updated state after saving to Redis
    """
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
    """
    Format summary results for different output types
    
    Creates structured output formats for both input summary and retrieval summary
    operations, including metadata and intermediate results for frontend display.
    
    Args:
        state: ReadState containing storage and user information
        aimessages: Generated summary message
        raw_results: Raw search/retrieval results
        
    Returns:
        tuple: (input_summary, retrieve_summary) formatted result dictionaries
    """
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
    """
    Generate quick input summary from retrieved information
    
    Performs fast retrieval and generates a quick summary response for user queries.
    This function prioritizes speed by only searching summary nodes and provides
    immediate feedback to users.
    
    Args:
        state: ReadState containing user query, storage configuration, and context
        
    Returns:
        ReadState: Dictionary containing summary results with status and metadata
    """
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
        "include": ["summaries", "communities"]  # MemorySummary 和 Community 同为高维度概括节点
    }

    try:
        if storage_type != "rag":

            async def _perceptual_search():
                service = PerceptualSearchService(
                    end_user_id=end_user_id,
                    memory_config=memory_config,
                )
                return await service.search(query=data, limit=5)

            hybrid_task = SearchService().execute_hybrid_search(
                **search_params,
                memory_config=memory_config,
                expand_communities=False,
            )
            perceptual_task = _perceptual_search()

            gather_results = await asyncio.gather(
                hybrid_task, perceptual_task, return_exceptions=True
            )
            hybrid_result = gather_results[0]
            perceptual_results = gather_results[1]

            # 处理 hybrid search 异常
            if isinstance(hybrid_result, Exception):
                raise hybrid_result
            retrieve_info, question, raw_results = hybrid_result

            # 处理感知记忆结果
            if isinstance(perceptual_results, Exception):
                logger.warning(f"[Input_Summary] perceptual search failed: {perceptual_results}")
                perceptual_results = []

            # 拼接感知记忆内容到 retrieve_info
            if perceptual_results and isinstance(perceptual_results, dict):
                perceptual_content = perceptual_results.get("content", "")
                if perceptual_content:
                    retrieve_info = f"{retrieve_info}\n\n<history-files>\n{perceptual_content}"
                    count = len(perceptual_results.get("memories", []))
                    logger.info(f"[Input_Summary] appended {count} perceptual memories (reranked)")

            # 调试：打印 community 检索结果数量
            if raw_results and isinstance(raw_results, dict):
                reranked = raw_results.get('reranked_results', {})
                community_hits = reranked.get('communities', [])
                logger.debug(f"[Input_Summary] community 命中数: {len(community_hits)}, "
                             f"summary 命中数: {len(reranked.get('summaries', []))}")
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
    duration = end - start
    log_time('检索', duration)
    return {"summary": summary}


async def Retrieve_Summary(state: ReadState) -> ReadState:
    """
    Generate comprehensive summary from retrieved expansion issues
    
    Processes retrieved expansion issues and generates a detailed summary using LLM.
    This function handles complex retrieval results and provides comprehensive answers
    based on expanded query results.
    
    Args:
        state: ReadState containing retrieve data with expansion issues
        
    Returns:
        ReadState: Dictionary containing comprehensive summary results
    """
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

    # Merge perceptual memory content
    perceptual_data = state.get("perceptual_data", {})
    perceptual_content = perceptual_data.get("content", "") if isinstance(perceptual_data, dict) else ""
    if perceptual_content:
        retrieve_info_str = f"{retrieve_info_str}\n\n<history-file-input>\n{perceptual_content}</history-file-input>"

    aimessages = await summary_llm(
        state,
        history,
        retrieve_info_str,
        'direct_summary_prompt.jinja2',
        'retrieve_summary', RetrieveSummaryResponse,
        "1"
    )
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

    # Fixed coroutine call - await first, then access return value
    summary_result = await summary_prompt(state, aimessages, retrieve_info_str)
    summary = summary_result[1]
    return {"summary": summary}


async def Summary(state: ReadState) -> ReadState:
    """
    Generate final comprehensive summary from verified data
    
    Creates the final summary using verified expansion issues and conversation history.
    This function processes verified data to generate the most comprehensive and
    accurate response to user queries.
    
    Args:
        state: ReadState containing verified data and query information
        
    Returns:
        ReadState: Dictionary containing final summary results
    """
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

    # Merge perceptual memory content
    perceptual_data = state.get("perceptual_data", {})
    perceptual_content = perceptual_data.get("content", "") if isinstance(perceptual_data, dict) else ""
    if perceptual_content:
        retrieve_info_str = f"{retrieve_info_str}\n\n<history-file-input>\n{perceptual_content}</history-file-input>"

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

    # Fixed coroutine call - await first, then access return value
    summary_result = await summary_prompt(state, aimessages, retrieve_info_str)
    summary = summary_result[1]
    return {"summary": summary}


async def Summary_fails(state: ReadState) -> ReadState:
    """
    Generate fallback summary when normal summary process fails
    
    Provides a fallback summary generation mechanism when the standard summary
    process encounters errors or fails to produce satisfactory results. Uses
    a specialized failure template to handle edge cases.
    
    Args:
        state: ReadState containing verified data and failure context
        
    Returns:
        ReadState: Dictionary containing fallback summary results
    """
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

    # Merge perceptual memory content
    perceptual_data = state.get("perceptual_data", {})
    perceptual_content = perceptual_data.get("content", "") if isinstance(perceptual_data, dict) else ""
    if perceptual_content:
        retrieve_info_str = f"{retrieve_info_str}\n\n<history-file-input>\n{perceptual_content}</history-file-input>"

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
