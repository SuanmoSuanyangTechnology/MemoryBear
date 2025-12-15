"""
Summary Tools for data summarization.

This module contains MCP tools for summarizing retrieved data and generating responses.
"""
import json
import re
import time
from typing import List

from pydantic import BaseModel, Field
from mcp.server.fastmcp import Context

from app.core.logging_config import get_agent_logger, log_time
from app.core.memory.agent.mcp_server.mcp_instance import mcp
from app.core.memory.agent.mcp_server.server import get_context_resource
from app.core.memory.agent.mcp_server.models.summary_models import (
    SummaryData,
    SummaryResponse,
    RetrieveSummaryData,
    RetrieveSummaryResponse
)
from app.core.memory.agent.utils.messages_tool import (
    Summary_messages_deal,
    Resolve_username
)
from app.core.rag.nlp.search import knowledge_retrieval
from dotenv import load_dotenv
import os

# 加载.env文件
load_dotenv()
logger = get_agent_logger(__name__)


@mcp.tool()
async def Summary(
    ctx: Context,
    context: str,
    usermessages: str,
    apply_id: str,
    group_id: str,
    storage_type: str = "",
    user_rag_memory_id: str = ""
) -> dict:
    """
    Summarize the verified data.
    
    Args:
        ctx: FastMCP context for dependency injection
        context: JSON string containing verified data
        usermessages: User messages identifier
        apply_id: Application identifier
        group_id: Group identifier
        storage_type: Storage type for the workspace (optional)
        user_rag_memory_id: User RAG memory identifier (optional)
        
    Returns:
        dict: Contains 'status' and 'summary_result'
    """
    start = time.time()
    
    try:
        # Extract services from context
        template_service = get_context_resource(ctx, 'template_service')
        session_service = get_context_resource(ctx, 'session_service')
        llm_client = get_context_resource(ctx, 'llm_client')
        
        # Resolve session ID
        sessionid = Resolve_username(usermessages)
        
        # Process context to extract answer and query
        answer_small, query = await Summary_messages_deal(context)

        
        start_time= time.time()
        history = await session_service.get_history(sessionid, apply_id, group_id)
        end_time=time.time()
        logger.info(f"Retrieve_Summary-REDIS搜索：{end_time - start_time}")
        data = {
            "query": query,
            "history": history,
            "retrieve_info": answer_small
        }

    except Exception as e:
        logger.error(
            f"Summary: initialization failed: {e}",
            exc_info=True
        )
        return {
            "status": "error",
            "summary_result": "信息不足，无法回答"
        }

    try:
        # Render template
        system_prompt = await template_service.render_template(
            template_name='summary_prompt.jinja2',
            operation_name='summary',
            data=data,
            query=query
        )
    except Exception as e:
        logger.error(
            f"Template rendering failed for Summary: {e}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": f"Prompt rendering failed: {str(e)}"
        }

    try:
        # Call LLM with structured response
        structured = await llm_client.response_structured(
            messages=[{"role": "system", "content": system_prompt}],
            response_model=SummaryResponse
        )

        aimessages = structured.query_answer or ""

    except Exception as e:
        logger.error(
            f"LLM call failed for Summary: {e}",
            exc_info=True
        )
        aimessages = ""

    try:
        # Save session
        if aimessages != "":
            await session_service.save_session(
            user_id=sessionid,
            query=query,
            apply_id=apply_id,
            group_id=group_id,
            ai_response=aimessages
        )
        logger.info(f"sessionid: {aimessages} 写入成功")
    except Exception as e:
        logger.error(
            f"sessionid: {sessionid} 写入失败，错误信息：{str(e)}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": str(e)
        }

    # Cleanup duplicate sessions
    await session_service.cleanup_duplicates()

    # Use fallback if empty
    if aimessages == '':
        aimessages = '信息不足，无法回答'

    logger.info(f"验证之后的总结==>>:{aimessages}")

    # Log execution time
    end = time.time()
    try:
        duration = end - start
    except Exception:
        duration = 0.0
    log_time('总结', duration)

    return {
        "status": "success",
        "summary_result": aimessages,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id
    }


@mcp.tool()
async def Retrieve_Summary(
    ctx: Context,
    context: dict,
    usermessages: str,
    apply_id: str,
    group_id: str,
    storage_type: str = "",
    user_rag_memory_id: str = ""
) -> dict:
    """
    Summarize data directly from retrieval results.

    Args:
        ctx: FastMCP context for dependency injection
        context: Dictionary containing Query and Expansion_issue from Retrieve
        usermessages: User messages identifier
        apply_id: Application identifier
        group_id: Group identifier
        storage_type: Storage type for the workspace (optional)
        user_rag_memory_id: User RAG memory identifier (optional)

    Returns:
        dict: Contains 'status' and 'summary_result'
    """
    start = time.time()

    try:
        # Extract services from context
        template_service = get_context_resource(ctx, 'template_service')
        session_service = get_context_resource(ctx, 'session_service')
        llm_client = get_context_resource(ctx, 'llm_client')

        # Resolve session ID
        sessionid = Resolve_username(usermessages)



        # Handle both 'content' and 'context' keys (LangGraph uses 'content')
        if isinstance(context, dict):
            if "content" in context:
                inner = context["content"]
                # If it's a JSON string, parse it
                if isinstance(inner, str):
                    try:
                        parsed = json.loads(inner)
                        logger.info("Retrieve_Summary: successfully parsed JSON")
                    except json.JSONDecodeError:
                        # Try unescaping first
                        try:
                            unescaped = inner.encode('utf-8').decode('unicode_escape')
                            parsed = json.loads(unescaped)
                            logger.info("Retrieve_Summary: parsed after unescaping")
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            logger.error(
                                f"Retrieve_Summary: parsing failed even after unescape: {e}"
                            )
                            context_dict = {"Query": "", "Expansion_issue": []}
                            parsed = None

                    if parsed:
                        # Check if parsed has 'context' wrapper
                        if isinstance(parsed, dict) and "context" in parsed:
                            context_dict = parsed["context"]
                        else:
                            context_dict = parsed
                elif isinstance(inner, dict):
                    context_dict = inner
                else:
                    context_dict = {"Query": "", "Expansion_issue": []}
            elif "context" in context:
                context_dict = context["context"] if isinstance(context["context"], dict) else context
            else:
                context_dict = context
        else:
            context_dict = {"Query": "", "Expansion_issue": []}

        query = context_dict.get("Query", "")
        expansion_issue = context_dict.get("Expansion_issue", [])

        # Extract retrieve_info from expansion_issue
        retrieve_info = []
        for item in expansion_issue:
            # Check for both Answer_Small and Answer_Samll (typo) for backward compatibility
            answer = None
            if isinstance(item, dict):
                if "Answer_Small" in item:
                    answer = item["Answer_Small"]
                elif "Answer_Samll" in item:
                    answer = item["Answer_Samll"]

                if answer is not None:
                    # Handle both string and list formats
                    if isinstance(answer, list):
                        # Join list of characters/strings into a single string
                        retrieve_info.append(''.join(str(x) for x in answer))
                    elif isinstance(answer, str):
                        retrieve_info.append(answer)
                    else:
                        retrieve_info.append(str(answer))

        # Join all retrieve_info into a single string
        retrieve_info_str = '\n\n'.join(retrieve_info) if retrieve_info else ""

        start_time=time.time()
        history = await session_service.get_history(sessionid, apply_id, group_id)
        # Override with empty list for now (as in original)
        end_time=time.time()
        logger.info(f"Retrieve_Summary-REDIS搜索：{end_time - start_time}")
    except Exception as e:
        logger.error(
            f"Retrieve_Summary: initialization failed: {e}",
            exc_info=True
        )
        return {
            "status": "error",
            "summary_result": "信息不足，无法回答"
        }

    try:
        # Render template
        system_prompt = await template_service.render_template(
            template_name='Retrieve_Summary_prompt.jinja2',
            operation_name='retrieve_summary',
            query=query,
            history=history,
            retrieve_info=retrieve_info_str
        )
    except Exception as e:
        logger.error(
            f"Template rendering failed for Retrieve_Summary: {e}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": f"Prompt rendering failed: {str(e)}"
        }

    try:
        # Call LLM with structured response
        structured = await llm_client.response_structured(
            messages=[{"role": "system", "content": system_prompt}],
            response_model=RetrieveSummaryResponse
        )

        # Handle case where structured response might be None or incomplete
        if structured and hasattr(structured, 'data') and structured.data:
            aimessages = structured.data.query_answer or ""
        else:
            logger.warning("Structured response is None or incomplete, using default message")
            aimessages = "信息不足，无法回答"


        # Check for insufficient information response
        if '信息不足，无法回答' not  in str(aimessages) or str(aimessages)!="":
            # Save session
            await session_service.save_session(
                user_id=sessionid,
                query=query,
                apply_id=apply_id,
                group_id=group_id,
                ai_response=aimessages
            )
            logger.info(f"sessionid: {aimessages} 写入成功")
    except Exception as e:
        logger.error(
            f"Retrieve_Summary: LLM call failed: {e}",
            exc_info=True
        )
        aimessages = ""
    # Cleanup duplicate sessions
    await session_service.cleanup_duplicates()

    # Use fallback if empty
    if aimessages == '':
        aimessages = '信息不足，无法回答'

    logger.info(f"检索之后的总结==>>:{aimessages}")

    # Log execution time
    end = time.time()
    try:
        duration = end - start
    except Exception:
        duration = 0.0
    log_time('检索总结', duration)

    # Emit intermediate output for frontend
    return {
        "status": "success",
        "summary_result": aimessages,
        "storage_type": storage_type,
        "user_rag_memory_id": user_rag_memory_id,
        "_intermediate": {
            "type": "retrieval_summary",
            "summary": aimessages,
            "query": query,
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
    }


@mcp.tool()
async def Input_Summary(
    ctx: Context,
    context: str,
    usermessages: str,
    search_switch: str,
    apply_id: str,
    group_id: str,
    storage_type: str = "",
    user_rag_memory_id: str = ""
) -> dict:
    """
    Generate a quick summary for direct input without verification.

    Args:
        ctx: FastMCP context for dependency injection
        context: String containing the input sentence
        usermessages: User messages identifier
        search_switch: Search switch value for routing ('2' for summaries only)
        apply_id: Application identifier
        group_id: Group identifier
        storage_type: Storage type for the workspace (e.g., 'rag', 'vector')
        user_rag_memory_id: User RAG memory identifier

    Returns:
        dict: Contains 'query_answer' with the summary result
    """
    start = time.time()
    logger.info(f"Input_Summary: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")

    # Initialize variables to avoid UnboundLocalError


    try:
        # Extract services from context
        template_service = get_context_resource(ctx, 'template_service')
        session_service = get_context_resource(ctx, 'session_service')
        llm_client = get_context_resource(ctx, 'llm_client')
        search_service = get_context_resource(ctx, 'search_service')

        # Check if llm_client is None
        if llm_client is None:
            error_msg = "LLM client is not available. Please check server configuration and SELECTED_LLM_ID environment variable."
            logger.error(error_msg)
            return error_msg

        # Resolve session ID
        sessionid = Resolve_username(usermessages) or ""
        sessionid = sessionid.replace('call_id_', '')

        start_time=time.time()
        history = await session_service.get_history(
            str(sessionid),
            str(apply_id),
            str(group_id)
        )
        end_time=time.time()
        logger.info(f"Input_Summary-REDIS搜索：{end_time - start_time}")
        # Override with empty list for now (as in original)

        # Log the raw context for debugging
        logger.info(f"Input_Summary: Received context type={type(context)}, value={context[:200] if isinstance(context, str) else context}")

        # Extract sentence from context
        # Context can be a string or might contain the sentence in various formats
        try:
            # Try to parse as JSON first
            if isinstance(context, str) and (context.startswith('{') or context.startswith('[')):
                try:
                    import json
                    context_dict = json.loads(context)
                    if isinstance(context_dict, dict):
                        query = context_dict.get('sentence', context_dict.get('content', context))
                    else:
                        query = context
                except json.JSONDecodeError:
                    # Not valid JSON, try regex
                    match = re.search(r"'sentence':\s*['\"]?(.*?)['\"]?\s*,", context)
                    query = match.group(1) if match else context
            else:
                query = context
        except Exception as e:
            logger.warning(f"Failed to extract query from context: {e}")
            query = context

        # Clean query
        query = str(query).strip().strip("\"'")

        logger.debug(f"Input_Summary: Extracted query='{query}' from context type={type(context)}")

        # Execute search based on search_switch and storage_type
        try:
            logger.info(f"search_switch: {search_switch}, storage_type: {storage_type}")

            # Prepare search parameters based on storage type
            search_params = {
                "group_id": group_id,
                "question": query,
                "return_raw_results": True
            }

            # Add storage-specific parameters

            '''检索'''
            if search_switch == '2':
                search_params["include"] = ["summaries"]
                if storage_type == "rag" and user_rag_memory_id:
                    raw_results = []
                    retrieve_info = ""
                    kb_config={
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
                        "reranker_id":os.getenv('reranker_id'),
                        "reranker_top_k": 10
                    }

                    retrieve_chunks_result = knowledge_retrieval(query, kb_config,[str(group_id)])
                    try:
                        retrieval_knowledge = [i.page_content for i in retrieve_chunks_result]
                        retrieve_info = '\n\n'.join(retrieval_knowledge)
                        raw_results=[retrieve_info]
                        logger.info(f"Input_Summary: Using RAG storage with memory_id={user_rag_memory_id}")
                    except:
                        retrieve_info=''
                        raw_results=['']
                        logger.info(f"知识库没有检索的内容{user_rag_memory_id}")
                else:
                    retrieve_info, question, raw_results = await search_service.execute_hybrid_search(**search_params)
                logger.info("Input_Summary: 使用 summary 进行检索")
            else:
                retrieve_info, question, raw_results = await search_service.execute_hybrid_search(**search_params)

        except Exception as e:
            logger.error(
                f"Input_Summary: hybrid_search failed, using empty results: {e}",
                exc_info=True
            )
            retrieve_info, question, raw_results = "", query, []


        # Render template
        system_prompt = await template_service.render_template(
            template_name='Retrieve_Summary_prompt.jinja2',
            operation_name='input_summary',
            query=query,
            history=history,
            retrieve_info=retrieve_info
        )

        # Call LLM with structured response
        try:
            structured = await llm_client.response_structured(
                messages=[{"role": "system", "content": system_prompt}],
                response_model=RetrieveSummaryResponse
            )
            aimessages = structured.data.query_answer or "信息不足，无法回答"
        except Exception as e:
            logger.error(
                f"Input_Summary: response_structured failed, using default answer: {e}",
                exc_info=True
            )
            aimessages = "信息不足，无法回答"

        logger.info(f"快速答案总结==>>:{storage_type}--{user_rag_memory_id}--{aimessages}")

        # Emit intermediate output for frontend
        return {
            "status": "success",
            "summary_result": aimessages,
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "_intermediate": {
                "type": "input_summary",
                "title": "快速答案",
                "summary": aimessages,
                "query": query,
                "raw_results": raw_results,
                "search_mode": "quick_search",
                "storage_type": storage_type,
                "user_rag_memory_id": user_rag_memory_id
            }
        }

    except Exception as e:
        logger.error(
            f"Input_Summary failed: {e}",
            exc_info=True
        )
        return {
            "status": "fail",
            "summary_result": "信息不足，无法回答",
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "error": str(e)
        }

    finally:
        # Log execution time
        end = time.time()
        try:
            duration = end - start
        except Exception:
            duration = 0.0
        log_time('检索', duration)


@mcp.tool()
async def Summary_fails(
    ctx: Context,
    context: str,
    usermessages: str,
    apply_id: str,
    group_id: str,
    storage_type: str = "",
    user_rag_memory_id: str = ""
) -> dict:
    """
    Handle workflow failure when summary cannot be generated.

    Args:
        ctx: FastMCP context for dependency injection
        context: Failure context string
        usermessages: User messages identifier
        apply_id: Application identifier
        group_id: Group identifier
        storage_type: Storage type for the workspace (optional)
        user_rag_memory_id: User RAG memory identifier (optional)

    Returns:
        dict: Contains 'query_answer' with failure message
    """
    try:
        # Extract services from context
        session_service = get_context_resource(ctx, 'session_service')

        # Parse session ID from usermessages
        usermessages_parts = usermessages.split('_')[1:]
        sessionid = '_'.join(usermessages_parts[:-1])

        # Cleanup duplicate sessions
        await session_service.cleanup_duplicates()

        logger.info("没有相关数据")
        logger.debug(f"Summary_fails called with apply_id: {apply_id}, group_id: {group_id}")
        
        return {
            "status": "success",
            "summary_result": "没有相关数据",
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
        
    except Exception as e:
        logger.error(
            f"Summary_fails failed: {e}",
            exc_info=True
        )
        return {
            "status": "fail",
            "summary_result": "没有相关数据",
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
            "error": str(e)
        }
