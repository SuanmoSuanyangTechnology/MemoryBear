"""
Retrieval Tools for database and context retrieval.

This module contains MCP tools for retrieving data using hybrid search.
"""

import os
import time

from app.core.logging_config import get_agent_logger, log_time
from app.core.memory.agent.mcp_server.mcp_instance import mcp
from app.core.memory.agent.mcp_server.server import get_context_resource
from app.core.memory.agent.utils.llm_tools import (
    deduplicate_entries,
    merge_to_key_value_pairs,
)
from app.core.memory.agent.utils.messages_tool import Retriev_messages_deal
from app.core.rag.nlp.search import knowledge_retrieval
from app.schemas.memory_config_schema import MemoryConfig
from dotenv import load_dotenv
from mcp.server.fastmcp import Context

load_dotenv()
logger = get_agent_logger(__name__)


@mcp.tool()
async def Retrieve(
    ctx: Context,
    context,
    usermessages: str,
    apply_id: str,
    group_id: str,
    memory_config: MemoryConfig,
    storage_type: str = "",
    user_rag_memory_id: str = "",
) -> dict:
    """
    Retrieve data from the database using hybrid search.
    
    Args:
        ctx: FastMCP context for dependency injection
        context: Dictionary or string containing query information
        usermessages: User messages identifier
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
        storage_type: Storage type for the workspace (e.g., 'rag', 'vector')
        user_rag_memory_id: User RAG memory identifier
        
    Returns:
        dict: Contains 'context' with Query and Expansion_issue results
    """
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
    start = time.time()
    logger.info(f"Retrieve: storage_type={storage_type}, user_rag_memory_id={user_rag_memory_id}")
    logger.info(f"Retrieve: context type={type(context)}, context={str(context)[:500]}")
    
    try:
        # Extract services from context
        search_service = get_context_resource(ctx, 'search_service')
        
        databases_anser = []
        
        # Handle both dict and string context
        if isinstance(context, dict):
            # Process dict context with extended questions
            all_items = []
            logger.info(f"Retrieve: context keys={list(context.keys())}")
            content, original = await Retriev_messages_deal(context)
            logger.info(f"Retrieve: after Retriev_messages_deal - content_type={type(content)}, content={str(content)[:300]}")
            logger.info(f"Retrieve: original='{original[:100] if original else 'EMPTY'}'")
            
            if not original:
                logger.warning(f"Retrieve: original query is empty! context={context}")
            
            # Extract all query items from content
            # content is like {original_question: [extended_questions...], ...}
            for key, values in content.items():
                if isinstance(values, list):
                    all_items.extend(values)
                elif isinstance(values, str):
                    all_items.append(values)
                elif values is not None:
                    # Fallback: convert non-empty non-list values to string
                    all_items.append(str(values))
            
            # Execute search for each question
            for idx, question in enumerate(all_items):
                try:
                    # Prepare search parameters based on storage type
                    search_params = {
                        "group_id": group_id,
                        "question": question,
                        "return_raw_results": True
                    }

                    # Add storage-specific parameters
                    if storage_type == "rag" and user_rag_memory_id:
                        retrieve_chunks_result = knowledge_retrieval(question, kb_config,[str(group_id)])
                        try:
                            retrieval_knowledge = [i.page_content for i in retrieve_chunks_result]
                            clean_content = '\n\n'.join(retrieval_knowledge)
                            cleaned_query=question
                            raw_results=clean_content
                            logger.info(f" Using RAG storage with memory_id={user_rag_memory_id}")
                        except:
                            clean_content = ''
                            raw_results=''
                            cleaned_query = question
                            logger.info(f"No content retrieved from knowledge base: {user_rag_memory_id}")
                    else:
                        clean_content, cleaned_query, raw_results = await search_service.execute_hybrid_search(
                            **search_params, memory_config=memory_config
                        )

                    databases_anser.append({
                        "Query_small": cleaned_query,
                        "Result_small": clean_content,
                        "_intermediate": {
                            "type": "search_result",
                            "query": cleaned_query,
                            "raw_results": raw_results,  
                            "index": idx + 1,
                            "total": len(all_items)
                        }
                    })
                except Exception as e:
                    logger.error(
                        f"Retrieve: hybrid_search failed for question '{question}': {e}",
                        exc_info=True
                    )
                    # Continue with empty result for this question
                    databases_anser.append({
                        "Query_small": question,
                        "Result_small": ""
                    })
            
            # Build initial database data structure
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
            
        else:
            # Handle string context (simple query)
            query = str(context).strip()
            
            try:
                # Prepare search parameters based on storage type
                search_params = {
                    "group_id": group_id,
                    "question": query,
                    "return_raw_results": True
                }
                
                # Add storage-specific parameters
                if storage_type == "rag" and user_rag_memory_id:
                    retrieve_chunks_result = knowledge_retrieval(query, kb_config,[str(group_id)])
                    try:
                        retrieval_knowledge = [i.page_content for i in retrieve_chunks_result]
                        clean_content = '\n\n'.join(retrieval_knowledge)
                        cleaned_query = query
                        raw_results = clean_content
                        logger.info(f" Using RAG storage with memory_id={user_rag_memory_id}")
                    except:
                        clean_content = ''
                        raw_results = ''
                        cleaned_query = query
                        logger.info(f"No content retrieved from knowledge base: {user_rag_memory_id}")
                else:
                    clean_content, cleaned_query, raw_results = await search_service.execute_hybrid_search(
                        **search_params, memory_config=memory_config
                    )
                # Keep structure for Verify/Retrieve_Summary compatibility
                dup_databases = {
                    "Query": cleaned_query,
                    "Expansion_issue": [{
                        "Query_small": cleaned_query,
                        "Answer_Small": clean_content,
                        "_intermediate": {
                            "type": "search_result",
                            "query": cleaned_query,
                            "raw_results": raw_results,
                            "index": 1,
                            "total": 1
                        }
                    }]
                }
            except Exception as e:
                logger.error(
                    f"Retrieve: hybrid_search failed for query '{query}': {e}",
                    exc_info=True
                )
                # Return empty results on failure
                dup_databases = {
                    "Query": query,
                    "Expansion_issue": []
                }
        
        logger.info(
            f"Retrieval: {storage_type}--{user_rag_memory_id}--Query={dup_databases.get('Query', '')}, "
            f"Expansion_issue count={len(dup_databases.get('Expansion_issue', []))}"
        )
        
        # Build result with intermediate outputs
        result = {
            "context": dup_databases,
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id
        }
        
        # Add intermediate outputs list if they exist
        intermediate_outputs = dup_databases.get('_intermediate_outputs', [])
        if intermediate_outputs:
            result['_intermediates'] = intermediate_outputs
            logger.info(f"Adding {len(intermediate_outputs)} intermediate outputs to result")
        else:
            logger.warning("No intermediate outputs found in dup_databases")
        
        return result
        
    except Exception as e:
        logger.error(
            f"Retrieve failed: {e}",
            exc_info=True
        )
        return {
            "context": {
                "Query": "",
                "Expansion_issue": []
            },
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
        log_time('Retrieval', duration)
