import asyncio
import json
from datetime import datetime, timedelta

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.core.memory.src.search import (
    search_by_temporal,
    search_by_keyword_temporal,
)


def extract_tool_message_content(response):
    """
    Extract ToolMessage content and tool names from agent response

    Parses agent response messages to extract tool execution results and metadata.
    Handles JSON parsing and provides structured access to tool output data.

    Args:
        response: Agent response dictionary containing messages

    Returns:
        dict: Dictionary containing tool_name and parsed content, or None if no tool message found
            - tool_name: Name of the executed tool
            - content: Parsed tool execution result (JSON or raw text)
    """
    messages = response.get('messages', [])

    for message in messages:
        if hasattr(message, 'tool_call_id') and hasattr(message, 'content'):
            # This is a ToolMessage
            tool_content = message.content
            tool_name = None

            # Try to get tool name
            if hasattr(message, 'name'):
                tool_name = message.name
            elif hasattr(message, 'tool_name'):
                tool_name = message.tool_name

            try:
                # Parse JSON content
                parsed_content = json.loads(tool_content)
                return {
                    'tool_name': tool_name,
                    'content': parsed_content
                }
            except json.JSONDecodeError:
                # If not JSON format, return content directly
                return {
                    'tool_name': tool_name,
                    'content': tool_content
                }

    return None


class TimeRetrievalInput(BaseModel):
    """
    Input schema for time retrieval tool

    Defines the expected input parameters for time-based retrieval operations.
    Used for validation and documentation of tool parameters.

    Attributes:
        context: User input query content for search
        end_user_id: Group ID for filtering search results, defaults to test user
    """
    context: str = Field(description="用户输入的查询内容")
    end_user_id: str = Field(default="88a459f5_text09", description="组ID，用于过滤搜索结果")


def create_time_retrieval_tool(end_user_id: str):
    """
    Create a TimeRetrieval tool with specific end_user_id (synchronous version) for searching statements by time range

    Creates a specialized time-based retrieval tool that searches for statements within
    specified time ranges. Includes field cleaning functionality to remove unnecessary
    metadata from search results.

    Args:
        end_user_id: User identifier for scoping search results

    Returns:
        function: Configured TimeRetrievalWithGroupId tool function
    """

    def clean_temporal_result_fields(data):
        """
        Clean unnecessary fields from temporal search results and modify structure

        Removes metadata fields that are not needed for end-user consumption and
        restructures the response format for better usability.
        
        Args:
            data: Data to be cleaned (dict, list, or other types)
            
        Returns:
            Cleaned data with unnecessary fields removed
        """
        # List of fields to filter out
        fields_to_remove = {
            'id', 'apply_id', 'user_id', 'chunk_id', 'created_at',
            'valid_at', 'invalid_at', 'statement_ids'
        }

        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if key == 'statements' and isinstance(value, dict) and 'statements' in value:
                    # Change statements: {"statements": [...]} to time_search: {"statements": [...]}
                    cleaned_value = clean_temporal_result_fields(value)
                    # Further change internal statements to time_search
                    if 'statements' in cleaned_value:
                        cleaned['results'] = {
                            'time_search': cleaned_value['statements']
                        }
                    else:
                        cleaned['results'] = cleaned_value
                elif key not in fields_to_remove:
                    cleaned[key] = clean_temporal_result_fields(value)
            return cleaned
        elif isinstance(data, list):
            return [clean_temporal_result_fields(item) for item in data]
        else:
            return data

    @tool
    def TimeRetrievalWithGroupId(context: str, start_date: str = None, end_date: str = None,
                                 end_user_id_param: str = None, clean_output: bool = True) -> str:
        """
        Optimized time retrieval tool, combines time range search only (synchronous version), automatically filters unnecessary metadata fields

        Performs time-based search operations with automatic metadata filtering. Supports
        flexible date range specification and provides clean, user-friendly output.

        Explicit parameters:
        - context: Query context content
        - start_date: Start time (optional, format: YYYY-MM-DD)
        - end_date: End time (optional, format: YYYY-MM-DD)
        - end_user_id_param: Group ID (optional, overrides default group ID)
        - clean_output: Whether to clean metadata fields from output
        - end_date needs to be obtained based on user description, output format uses strftime("%Y-%m-%d")

        Returns:
            str: JSON formatted search results with temporal data
        """

        async def _async_search():
            # Use passed parameters or default values
            actual_end_user_id = end_user_id_param or end_user_id
            actual_end_date = end_date or datetime.now().strftime("%Y-%m-%d")
            actual_start_date = start_date or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            
            # Basic time search
            results = await search_by_temporal(
                end_user_id=actual_end_user_id,
                start_date=actual_start_date,
                end_date=actual_end_date,
                limit=10
            )
            
            # Clean unnecessary fields from results
            if clean_output:
                cleaned_results = clean_temporal_result_fields(results)
            else:
                cleaned_results = results

            return json.dumps(cleaned_results, ensure_ascii=False, indent=2)

        return asyncio.run(_async_search())

    @tool
    def KeywordTimeRetrieval(context: str, days_back: int = 7, start_date: str = None, end_date: str = None,
                             clean_output: bool = True) -> str:
        """
        Optimized keyword time retrieval tool, combines keyword and time range search (synchronous version), automatically filters unnecessary metadata fields

        Performs combined keyword and temporal search operations with automatic metadata
        filtering. Provides more targeted search results by combining content relevance
        with time-based filtering.

        Explicit parameters:
        - context: Query content for keyword matching
        - days_back: Number of days to search backwards, default 7 days
        - start_date: Start time (optional, format: YYYY-MM-DD)
        - end_date: End time (optional, format: YYYY-MM-DD)
        - clean_output: Whether to clean metadata fields from output
        - end_date needs to be obtained based on user description, output format uses strftime("%Y-%m-%d")

        Returns:
            str: JSON formatted search results combining keyword and temporal data
        """

        async def _async_search():
            actual_end_date = end_date or datetime.now().strftime("%Y-%m-%d")
            actual_start_date = start_date or (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

            # Keyword time search
            results = await search_by_keyword_temporal(
                query_text=context,
                end_user_id=end_user_id,
                start_date=actual_start_date,
                end_date=actual_end_date,
                limit=15
            )
            
            # Clean unnecessary fields from results
            if clean_output:
                cleaned_results = clean_temporal_result_fields(results)
            else:
                cleaned_results = results

            return json.dumps(cleaned_results, ensure_ascii=False, indent=2)

        return asyncio.run(_async_search())

    return TimeRetrievalWithGroupId


def create_hybrid_retrieval_tool_async(memory_config, **search_params):
    """
    Create hybrid retrieval tool using run_hybrid_search for hybrid retrieval, optimize output format and filter unnecessary fields

    Creates an advanced hybrid search tool that combines multiple search strategies
    (keyword, vector, hybrid) with automatic result cleaning and formatting.
    
    Args:
        memory_config: Memory configuration object containing LLM and search settings
        **search_params: Search parameters including end_user_id, limit, include, etc.

    Returns:
        function: Configured HybridSearch tool function with async capabilities
    """

    def clean_result_fields(data):
        """
        Recursively clean unnecessary fields from results

        Removes metadata fields that are not needed for end-user consumption,
        improving readability and reducing response size.
        
        Args:
            data: Data to be cleaned (can be dict, list, or other types)
            
        Returns:
            Cleaned data with unnecessary fields removed
        """
        # List of fields to filter out
        # TODO: fact_summary functionality temporarily disabled, will be enabled after future development
        fields_to_remove = {
            'invalid_at', 'valid_at', 'chunk_id_from_rel', 'entity_ids',
            'expired_at', 'created_at', 'chunk_id', 'id', 'apply_id',
            'user_id', 'statement_ids', 'updated_at', "chunk_ids", "fact_summary"
        }

        if isinstance(data, dict):
            # Clean dictionary
            cleaned = {}
            for key, value in data.items():
                if key not in fields_to_remove:
                    cleaned[key] = clean_result_fields(value)  # Recursively clean nested data
            return cleaned
        elif isinstance(data, list):
            # Clean each element in list
            return [clean_result_fields(item) for item in data]
        else:
            # Return other types directly
            return data

    @tool
    async def HybridSearch(
        context: str, 
        search_type: str = "hybrid",
        limit: int = 10,
        end_user_id: str = None,
        rerank_alpha: float = 0.6,
        use_forgetting_rerank: bool = False,
        use_llm_rerank: bool = False,
        clean_output: bool = True  # New: whether to clean output fields
    ) -> str:
        """
        Optimized hybrid retrieval tool, supports keyword, vector and hybrid search, automatically filters unnecessary metadata fields

        Provides comprehensive search capabilities combining multiple search strategies
        with intelligent result ranking and automatic metadata filtering for clean output.
        
        Args:
            context: Query content for search
            search_type: Search type ('keyword', 'embedding', 'hybrid')
            limit: Result quantity limit
            end_user_id: Group ID for filtering search results
            rerank_alpha: Reranking weight parameter for result scoring
            use_forgetting_rerank: Whether to use forgetting-based reranking
            use_llm_rerank: Whether to use LLM-based reranking
            clean_output: Whether to clean metadata fields from output

        Returns:
            str: JSON formatted comprehensive search results
        """
        try:
            # Import run_hybrid_search function
            from app.core.memory.src.search import run_hybrid_search
            
            # Merge parameters, prioritize passed parameters
            final_params = {
                "query_text": context,
                "search_type": search_type,
                "end_user_id": end_user_id or search_params.get("end_user_id"),
                "limit": limit or search_params.get("limit", 10),
                "include": search_params.get("include", ["summaries", "statements", "chunks", "entities"]),
                "output_path": None,  # Don't save to file
                "memory_config": memory_config,
                "rerank_alpha": rerank_alpha,
                "use_forgetting_rerank": use_forgetting_rerank,
                "use_llm_rerank": use_llm_rerank
            }
            
            # Execute hybrid retrieval
            raw_results = await run_hybrid_search(**final_params)
            
            # Clean unnecessary fields from results
            if clean_output:
                cleaned_results = clean_result_fields(raw_results)
            else:
                cleaned_results = raw_results
            
            # Format return results
            formatted_results = {
                "search_query": context,
                "search_type": search_type,
                "results": cleaned_results
            }

            return json.dumps(formatted_results, ensure_ascii=False, indent=2, default=str)

        except Exception as e:
            error_result = {
                "error": f"混合检索失败: {str(e)}",
                "search_query": context,
                "search_type": search_type,
                "timestamp": datetime.now().isoformat()
            }
            return json.dumps(error_result, ensure_ascii=False, indent=2)

    return HybridSearch


def create_hybrid_retrieval_tool_sync(memory_config, **search_params):
    """
    Create synchronous version of hybrid retrieval tool, optimize output format and filter unnecessary fields

    Creates a synchronous wrapper around the async hybrid search functionality,
    making it compatible with synchronous tool execution environments.
    
    Args:
        memory_config: Memory configuration object containing search settings
        **search_params: Search parameters for configuration

    Returns:
        function: Configured HybridSearchSync tool function
    """

    @tool
    def HybridSearchSync(
            context: str,
            search_type: str = "hybrid",
            limit: int = 10,
            end_user_id: str = None,
            clean_output: bool = True
    ) -> str:
        """
        Optimized hybrid retrieval tool (synchronous version), automatically filters unnecessary metadata fields

        Provides the same hybrid search capabilities as the async version but in a
        synchronous execution context. Automatically handles async-to-sync conversion.
        
        Args:
            context: Query content for search
            search_type: Search type ('keyword', 'embedding', 'hybrid')
            limit: Result quantity limit
            end_user_id: Group ID for filtering search results
            clean_output: Whether to clean metadata fields from output

        Returns:
            str: JSON formatted search results
        """

        async def _async_search():
            # Create async tool and execute
            async_tool = create_hybrid_retrieval_tool_async(memory_config, **search_params)
            return await async_tool.ainvoke({
                "context": context,
                "search_type": search_type,
                "limit": limit,
                "end_user_id": end_user_id,
                "clean_output": clean_output
            })

        return asyncio.run(_async_search())

    return HybridSearchSync
