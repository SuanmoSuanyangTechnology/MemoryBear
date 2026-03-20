# ===== 标准库 =====
import asyncio
import json
import os

# ===== 第三方库 =====
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.langgraph_graph.tools.tool import (
    create_hybrid_retrieval_tool_sync,
    create_time_retrieval_tool,
    extract_tool_message_content,
)
from app.core.memory.agent.services.search_service import SearchService
from app.core.memory.agent.utils.llm_tools import (
    ReadState,
    deduplicate_entries,
    merge_to_key_value_pairs,
)
from app.core.rag.nlp.search import knowledge_retrieval
from app.db import get_db_context
from app.schemas import model_schema
from app.services.memory_config_service import MemoryConfigService
from app.services.model_service import ModelConfigService

logger = get_agent_logger(__name__)


async def rag_config(state):
    """
    Configure RAG (Retrieval-Augmented Generation) settings
    
    Creates configuration for knowledge base retrieval including similarity thresholds,
    weights, and reranker settings.
    
    Args:
        state: Current state containing user_rag_memory_id
        
    Returns:
        dict: RAG configuration dictionary
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
    Retrieve knowledge using RAG approach
    
    Performs knowledge retrieval from configured knowledge bases using the
    provided question and returns formatted results.
    
    Args:
        state: Current state containing configuration
        question: Question to search for
        
    Returns:
        tuple: (retrieval_knowledge, clean_content, cleaned_query, raw_results)
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
    except Exception:
        retrieval_knowledge = []
        clean_content = ''
        raw_results = ''
        cleaned_query = question
        logger.info(f"No content retrieved from knowledge base: {user_rag_memory_id}")
    return retrieval_knowledge, clean_content, cleaned_query, raw_results


async def llm_infomation(state: ReadState) -> ReadState:
    """
    Get LLM configuration information from state
    
    Retrieves model configuration details including model ID and tenant ID
    from the memory configuration in the current state.
    
    Args:
        state: ReadState containing memory configuration
        
    Returns:
        ReadState: Model configuration as Pydantic model
    """
    memory_config = state.get('memory_config', None)
    model_id = memory_config.llm_model_id
    tenant_id = memory_config.tenant_id

    # Use existing memory_config instead of re-querying database
    # or use thread-safe database access
    with get_db_context() as db:
        result_orm = ModelConfigService.get_model_by_id(db=db, model_id=model_id, tenant_id=tenant_id)
        result_pydantic = model_schema.ModelConfig.model_validate(result_orm)
    return result_pydantic


async def clean_databases(data) -> str:
    """
    Simplified database search result cleaning function
    
    Processes and cleans search results from various sources including
    reranked results and time-based search results. Extracts text content
    from structured data and returns as formatted string.
    
    Args:
        data: Search result data (can be string, dict, or other types)
        
    Returns:
        str: Cleaned content string
    """
    try:
        # Parse JSON string
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return data

        if not isinstance(data, dict):
            return str(data)

        # Get result data
        # with open("搜索结果.json","w",encoding='utf-8') as f:
        #     f.write(json.dumps(data, indent=4, ensure_ascii=False))
        results = data.get('results', data)
        if not isinstance(results, dict):
            return str(results)

        # Collect all content
        content_list = []

        # Process reranked results
        reranked = results.get('reranked_results', {})
        if reranked:
            for category in ['summaries', 'communities', 'statements', 'chunks', 'entities']:
                items = reranked.get(category, [])
                if isinstance(items, list):
                    content_list.extend(items)
        # Process time search results
        time_search = results.get('time_search', {})
        if time_search:
            if isinstance(time_search, dict):
                statements = time_search.get('statements', time_search.get('time_search', []))
                if isinstance(statements, list):
                    content_list.extend(statements)
            elif isinstance(time_search, list):
                content_list.extend(time_search)

        # Extract text content，对 community 按 name 去重（多次 tool 调用会产生重复）
        text_parts = []
        seen_community_names = set()
        for item in content_list:
            if isinstance(item, dict):
                # community 节点用 name 去重
                if 'member_count' in item or 'core_entities' in item:
                    community_name = item.get('name') or item.get('id', '')
                    if community_name in seen_community_names:
                        continue
                    seen_community_names.add(community_name)
                text = item.get('statement') or item.get('content') or item.get('summary', '')
                if text:
                    text_parts.append(text)
            elif isinstance(item, str):
                text_parts.append(item)

        return '\n'.join(text_parts).strip()

    except Exception as e:
        logger.error(f"clean_databases failed: {e}", exc_info=True)
        return str(data)


async def retrieve_nodes(state: ReadState) -> ReadState:
    """
    Retrieve information using simplified search approach
    
    Processes extended problems from previous nodes and performs retrieval
    using either RAG or hybrid search based on storage type. Handles concurrent
    processing of multiple questions and deduplicates results.
    
    Args:
        state: ReadState containing problem extensions and configuration
        
    Returns:
        ReadState: Updated state with retrieval results and intermediate outputs
    """

    problem_extension = state.get('problem_extension', '')['context']
    storage_type = state.get('storage_type', '')
    user_rag_memory_id = state.get('user_rag_memory_id', '')
    end_user_id = state.get('end_user_id', '')
    memory_config = state.get('memory_config', None)
    original = state.get('data', '')
    problem_list = []
    for key, values in problem_extension.items():
        for data in values:
            problem_list.append(data)
    logger.info(f"Retrieve: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")

    # Create async task to process individual questions
    async def process_question_nodes(idx, question):
        try:
            # Prepare search parameters based on storage type
            search_params = {
                "end_user_id": end_user_id,
                "question": question,
                "return_raw_results": True
            }
            if storage_type == "rag" and user_rag_memory_id:
                retrieval_knowledge, clean_content, cleaned_query, raw_results = await rag_knowledge(state, question)
            else:
                clean_content, cleaned_query, raw_results = await SearchService().execute_hybrid_search(
                    **search_params, memory_config=memory_config
                )

            return {
                "Query_small": cleaned_query,
                "Result_small": clean_content,
                "_intermediate": {
                    "type": "search_result",
                    "query": cleaned_query,
                    "raw_results": raw_results,
                    "index": idx + 1,
                    "total": len(problem_list)
                }
            }

        except Exception as e:
            logger.error(
                f"Retrieve: hybrid_search failed for question '{question}': {e}",
                exc_info=True
            )
            # Return empty result for this question
            return {
                "Query_small": question,
                "Result_small": "",
                "_intermediate": {
                    "type": "search_result",
                    "query": question,
                    "raw_results": [],
                    "index": idx + 1,
                    "total": len(problem_list)
                }
            }

    # Process all questions concurrently
    tasks = [process_question_nodes(idx, question) for idx, question in enumerate(problem_list)]
    databases_anser = await asyncio.gather(*tasks)
    databases_data = {
        "Query": original,
        "Expansion_issue": databases_anser
    }

    # Collect intermediate outputs before deduplication
    intermediate_outputs = []
    for item in databases_anser:
        if '_intermediate' in item:
            intermediate_outputs.append(item['_intermediate'])

    # Deduplicate and merge results
    deduplicated_data = deduplicate_entries(databases_data['Expansion_issue'])
    deduplicated_data_merged = merge_to_key_value_pairs(
        deduplicated_data,
        'Query_small',
        'Result_small'
    )

    # Restructure for Verify/Retrieve_Summary compatibility
    keys, val = [], []
    for item in deduplicated_data_merged:
        for items_key, items_value in item.items():
            keys.append(items_key)
            val.append(items_value)

    send_verify = []
    for i, j in zip(keys, val, strict=False):
        if j != ['']:
            send_verify.append({
                "Query_small": i,
                "Answer_Small": j
            })

    dup_databases = {
        "Query": original,
        "Expansion_issue": send_verify,
        "_intermediate_outputs": intermediate_outputs  # Preserve intermediate outputs
    }

    logger.info(f"Collected {len(intermediate_outputs)} intermediate outputs from search results")
    return {'retrieve': dup_databases}


async def retrieve(state: ReadState) -> ReadState:
    """
    Advanced retrieve function using LangChain agents and tools
    
    Uses LangChain agents with specialized retrieval tools (time-based and hybrid)
    to perform sophisticated information retrieval. Supports both RAG and traditional
    memory storage approaches with concurrent processing and result deduplication.
    
    Args:
        state: ReadState containing problem extensions and configuration
        
    Returns:
        ReadState: Updated state with retrieval results and intermediate outputs
    """
    # Get end_user_id from state
    import time
    start = time.time()
    problem_extension = state.get('problem_extension', '')['context']
    storage_type = state.get('storage_type', '')
    user_rag_memory_id = state.get('user_rag_memory_id', '')
    end_user_id = state.get('end_user_id', '')
    memory_config = state.get('memory_config', None)
    original = state.get('data', '')
    problem_list = []
    for key, values in problem_extension.items():
        for data in values:
            problem_list.append(data)
    logger.info(f"Retrieve: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")
    databases_anser = []

    async def get_llm_info():
        with get_db_context() as db:  # 使用同步数据库上下文管理器
            config_service = MemoryConfigService(db)
            return await llm_infomation(state)

    llm_config = await get_llm_info()
    api_key_obj = llm_config.api_keys[0]
    api_key = api_key_obj.api_key
    api_base = api_key_obj.api_base
    model_name = api_key_obj.model_name
    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=api_base,
        temperature=0.2,
    )

    time_retrieval_tool = create_time_retrieval_tool(end_user_id)
    search_params = {
        "end_user_id": end_user_id,
        "return_raw_results": True,
        "include": ["summaries", "statements", "chunks", "entities", "communities"],
    }
    hybrid_retrieval = create_hybrid_retrieval_tool_sync(memory_config, **search_params)
    agent = create_agent(
        llm,
        tools=[time_retrieval_tool, hybrid_retrieval],
        system_prompt=f"我是检索专家，可以根据适合的工具进行检索。当前使用的end_user_id是: {end_user_id}"
    )

    # Create async task to process individual questions
    import asyncio

    # Define semaphore at module level to limit maximum concurrency
    SEMAPHORE = asyncio.Semaphore(5)  # Limit to maximum 5 concurrent database operations

    async def process_question(idx, question):
        async with SEMAPHORE:  # Limit concurrency
            try:
                if storage_type == "rag" and user_rag_memory_id:
                    retrieval_knowledge, clean_content, cleaned_query, raw_results = await rag_knowledge(state,
                                                                                                         question)
                else:
                    cleaned_query = question
                    # Use asyncio to run synchronous agent.invoke in thread pool
                    import asyncio
                    response = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: agent.invoke({"messages": question})
                    )
                    tool_results = extract_tool_message_content(response)
                    if tool_results == None:
                        raw_results = []
                        clean_content = ''
                    else:
                        raw_results = tool_results['content']
                        clean_content = await clean_databases(raw_results)

                        # 社区展开：从 tool 返回结果中提取命中的 community，
                        # 沿 BELONGS_TO_COMMUNITY 关系拉取关联 Statement 追加到 clean_content
                        _expanded_stmts_to_write = []
                        try:
                            results_dict = raw_results.get('results', {}) if isinstance(raw_results, dict) else {}
                            reranked = results_dict.get('reranked_results', {})
                            community_hits = reranked.get('communities', [])
                            if not community_hits:
                                community_hits = results_dict.get('communities', [])
                            if community_hits:
                                from app.core.memory.agent.services.search_service import expand_communities_to_statements
                                _expanded_stmts_to_write, new_texts = await expand_communities_to_statements(
                                    community_results=community_hits,
                                    end_user_id=end_user_id,
                                    existing_content=clean_content,
                                )
                                if new_texts:
                                    clean_content = clean_content + '\n' + '\n'.join(new_texts)
                        except Exception as parse_err:
                            logger.warning(f"[Retrieve] 解析社区命中结果失败，跳过展开: {parse_err}")

                        try:
                            raw_results = raw_results['results']
                            # 写回展开结果，接口返回中可见（已在 helper 中清洗过字段）
                            if _expanded_stmts_to_write and isinstance(raw_results, dict):
                                raw_results.setdefault('reranked_results', {})['expanded_statements'] = _expanded_stmts_to_write
                        except Exception:
                            raw_results = []

                return {
                    "Query_small": cleaned_query,
                    "Result_small": clean_content,
                    "_intermediate": {
                        "type": "search_result",
                        "query": cleaned_query,
                        "raw_results": raw_results,
                        "index": idx + 1,
                        "total": len(problem_list)
                    }
                }

            except Exception as e:
                logger.error(
                    f"Retrieve: hybrid_search failed for question '{question}': {e}",
                    exc_info=True
                )
                # Return empty result for this question
                return {
                    "Query_small": question,
                    "Result_small": "",
                    "_intermediate": {
                        "type": "search_result",
                        "query": question,
                        "raw_results": [],
                        "index": idx + 1,
                        "total": len(problem_list)
                    }
                }

    # Process all questions concurrently
    import asyncio
    tasks = [process_question(idx, question) for idx, question in enumerate(problem_list)]
    databases_anser = await asyncio.gather(*tasks)
    databases_data = {
        "Query": original,
        "Expansion_issue": databases_anser
    }

    # Collect intermediate outputs before deduplication
    intermediate_outputs = []
    for item in databases_anser:
        if '_intermediate' in item:
            intermediate_outputs.append(item['_intermediate'])

    # Deduplicate and merge results
    deduplicated_data = deduplicate_entries(databases_data['Expansion_issue'])
    deduplicated_data_merged = merge_to_key_value_pairs(
        deduplicated_data,
        'Query_small',
        'Result_small'
    )

    # Restructure for Verify/Retrieve_Summary compatibility
    keys, val = [], []
    for item in deduplicated_data_merged:
        for items_key, items_value in item.items():
            keys.append(items_key)
            val.append(items_value)

    send_verify = []
    for i, j in zip(keys, val, strict=False):
        if j != ['']:
            send_verify.append({
                "Query_small": i,
                "Answer_Small": j
            })

    dup_databases = {
        "Query": original,
        "Expansion_issue": send_verify,
        "_intermediate_outputs": intermediate_outputs  # Preserve intermediate outputs
    }
    # with open('retrieve_text.json', 'w') as f:
    #     json.dump(dup_databases, f, indent=4)
    logger.info(f"Collected {len(intermediate_outputs)} intermediate outputs from search results")
    return {'retrieve': dup_databases}
