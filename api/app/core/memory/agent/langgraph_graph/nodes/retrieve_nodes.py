# ===== 标准库 =====
import asyncio
import json
import os

# ===== 第三方库 =====
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from app.core.logging_config import get_agent_logger
from app.db import get_db, get_db_context

from app.schemas import model_schema
from app.services.memory_config_service import MemoryConfigService
from app.services.model_service import ModelConfigService

from app.core.memory.agent.services.search_service import SearchService
from app.core.memory.agent.utils.llm_tools import (
    COUNTState,
    ReadState,
    deduplicate_entries,
    merge_to_key_value_pairs,
)
from app.core.memory.agent.langgraph_graph.tools.tool import (
    create_hybrid_retrieval_tool_sync,
    create_time_retrieval_tool,
    extract_tool_message_content,
)

from app.core.rag.nlp.search import knowledge_retrieval

logger = get_agent_logger(__name__)
db = next(get_db())



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
async def rag_knowledge(state,question):
    kb_config = await rag_config(state)
    end_user_id = state.get('end_user_id', '')
    user_rag_memory_id=state.get("user_rag_memory_id",'')
    retrieve_chunks_result = knowledge_retrieval(question, kb_config, [str(end_user_id)])
    try:
        retrieval_knowledge = [i.page_content for i in retrieve_chunks_result]
        clean_content = '\n\n'.join(retrieval_knowledge)
        cleaned_query = question
        raw_results = clean_content
        logger.info(f" Using RAG storage with memory_id={user_rag_memory_id}")
    except  Exception :
        retrieval_knowledge=[]
        clean_content = ''
        raw_results = ''
        cleaned_query = question
        logger.info(f"No content retrieved from knowledge base: {user_rag_memory_id}")
    return retrieval_knowledge,clean_content,cleaned_query,raw_results


async def llm_infomation(state: ReadState) -> ReadState:
    memory_config = state.get('memory_config', None)
    model_id = memory_config.llm_model_id
    tenant_id = memory_config.tenant_id

    # 使用现有的 memory_config 而不是重新查询数据库
    # 或者使用线程安全的数据库访问
    with get_db_context() as db:
        result_orm = ModelConfigService.get_model_by_id(db=db, model_id=model_id, tenant_id=tenant_id)
        result_pydantic = model_schema.ModelConfig.model_validate(result_orm)
    return result_pydantic


async def clean_databases(data) -> str:
    """
    简化的数据库搜索结果清理函数
    
    Args:
        data: 搜索结果数据
        
    Returns:
        清理后的内容字符串
    """
    try:
        # 解析JSON字符串
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return data

        if not isinstance(data, dict):
            return str(data)

        # 获取结果数据
        # with open("搜索结果.json","w",encoding='utf-8') as f:
        #     f.write(json.dumps(data, indent=4, ensure_ascii=False))
        results = data.get('results', data)
        if not isinstance(results, dict):
            return str(results)

        # 收集所有内容
        content_list = []
        
        # 处理重排序结果
        reranked = results.get('reranked_results', {})
        if reranked:
            for category in ['summaries', 'statements', 'chunks', 'entities']:
                items = reranked.get(category, [])
                if isinstance(items, list):
                    content_list.extend(items)
        # 处理时间搜索结果
        time_search = results.get('time_search', {})
        if time_search:
            if isinstance(time_search, dict):
                statements = time_search.get('statements', time_search.get('time_search', []))
                if isinstance(statements, list):
                    content_list.extend(statements)
            elif isinstance(time_search, list):
                content_list.extend(time_search)

        # 提取文本内容
        text_parts = []
        for item in content_list:
            if isinstance(item, dict):
                text = item.get('statement') or item.get('content', '')
                if text:
                    text_parts.append(text)
            elif isinstance(item, str):
                text_parts.append(item)


        return '\n'.join(text_parts).strip()

    except Exception as e:
        logger.error(f"clean_databases failed: {e}", exc_info=True)
        return str(data)


async def retrieve_nodes(state: ReadState) -> ReadState:

    '''

    模型信息
    '''

    problem_extension=state.get('problem_extension', '')['context']
    storage_type=state.get('storage_type', '')
    user_rag_memory_id=state.get('user_rag_memory_id', '')
    end_user_id=state.get('end_user_id', '')
    memory_config = state.get('memory_config', None)
    original=state.get('data', '')
    problem_list=[]
    for key,values in problem_extension.items():
        for data in values:
            problem_list.append(data)
    logger.info(f"Retrieve: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")
    # 创建异步任务处理单个问题
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

    # 并发处理所有问题
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
        if j!=['']:
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
    return {'retrieve':dup_databases}




async def retrieve(state: ReadState) -> ReadState:
    # 从state中获取end_user_id
    import time
    start=time.time()
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
    search_params = { "end_user_id": end_user_id, "return_raw_results": True }
    hybrid_retrieval=create_hybrid_retrieval_tool_sync(memory_config, **search_params)
    agent = create_agent(
        llm,
        tools=[time_retrieval_tool,hybrid_retrieval],
        system_prompt=f"我是检索专家，可以根据适合的工具进行检索。当前使用的end_user_id是: {end_user_id}"
    )

    # 创建异步任务处理单个问题
    import asyncio

    # 在模块级别定义信号量，限制最大并发数
    SEMAPHORE = asyncio.Semaphore(5)  # 限制最多5个并发数据库操作

    async def process_question(idx, question):
        async with SEMAPHORE:  # 限制并发
            try:
                if storage_type == "rag" and user_rag_memory_id:
                    retrieval_knowledge, clean_content, cleaned_query, raw_results = await rag_knowledge(state, question)
                else:
                    cleaned_query = question
                    # 使用 asyncio 在线程池中运行同步的 agent.invoke
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

                        try:
                            raw_results = raw_results['results']
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

    # 并发处理所有问题
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


