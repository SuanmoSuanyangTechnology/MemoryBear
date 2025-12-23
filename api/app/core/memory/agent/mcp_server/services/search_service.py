"""
Search Service for executing hybrid search and processing results.

This service provides clean search result processing with content extraction
and deduplication.
"""

from typing import TYPE_CHECKING, List, Optional, Tuple

from app.core.logging_config import get_agent_logger
from app.core.memory.src.search import run_hybrid_search
from app.core.memory.utils.data.text_utils import escape_lucene_query

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig

logger = get_agent_logger(__name__)


class SearchService:
    """Service for executing hybrid search and processing results."""

    def __init__(self, memory_config: "MemoryConfig" = None):
        """
        Initialize the search service.
        
        Args:
            memory_config: Optional MemoryConfig for embedding model configuration.
                          If not provided, must be passed to execute_hybrid_search.
        """
        self.memory_config = memory_config
        logger.info("SearchService initialized")
    
    def extract_content_from_result(self, result: dict) -> str:
        """
        Extract only meaningful content from search results, dropping all metadata.
        
        Extraction rules by node type:
        - Statements: extract 'statement' field
        - Entities: extract 'name' and 'fact_summary' fields
        - Summaries: extract 'content' field
        - Chunks: extract 'content' field
        
        Args:
            result: Search result dictionary
            
        Returns:
            Clean content string without metadata
        """
        if not isinstance(result, dict):
            return str(result)
        
        content_parts = []
        
        # Statements: extract statement field
        if 'statement' in result and result['statement']:
            content_parts.append(result['statement'])
        
        # Summaries/Chunks: extract content field
        if 'content' in result and result['content']:
            content_parts.append(result['content'])
        
        # Entities: extract name and fact_summary (commented out in original)
        # if 'name' in result and result['name']:
        #     content_parts.append(result['name'])
        #     if result.get('fact_summary'):
        #         content_parts.append(result['fact_summary'])
        
        # Return concatenated content or empty string
        return '\n'.join(content_parts) if content_parts else ""
    
    def clean_query(self, query: str) -> str:
        """
        Clean and escape query text for Lucene.
        
        - Removes wrapping quotes
        - Removes newlines and carriage returns
        - Applies Lucene escaping
        
        Args:
            query: Raw query string
            
        Returns:
            Cleaned and escaped query string
        """
        q = str(query).strip()
        
        # Remove wrapping quotes
        if (q.startswith("'") and q.endswith("'")) or (
            q.startswith('"') and q.endswith('"')
        ):
            q = q[1:-1]
        
        # Remove newlines and carriage returns
        q = q.replace('\r', ' ').replace('\n', ' ').strip()
        
        # Apply Lucene escaping
        q = escape_lucene_query(q)
        
        return q
    
    async def execute_hybrid_search(
        self,
        group_id: str,
        question: str,
        limit: int = 15,
        search_type: str = "hybrid",
        include: Optional[List[str]] = None,
        rerank_alpha: float = 0.4,
        output_path: str = "search_results.json",
        return_raw_results: bool = False,
        memory_config: "MemoryConfig" = None,
    ) -> Tuple[str, str, Optional[dict]]:
        """
        Execute hybrid search and return clean content.
        
        Args:
            group_id: Group identifier for filtering results
            question: Search query text
            limit: Maximum number of results to return (default: 5)
            search_type: Type of search - "hybrid", "keyword", or "embedding" (default: "hybrid")
            include: List of result types to include (default: ["statements", "chunks", "entities", "summaries"])
            rerank_alpha: Weight for BM25 scores in reranking (default: 0.4)
            output_path: Path to save search results (default: "search_results.json")
            return_raw_results: If True, also return the raw search results as third element (default: False)
            memory_config: MemoryConfig object for embedding model. Falls back to self.memory_config if not provided.
        
        Returns:
            Tuple of (clean_content, cleaned_query, raw_results)
            raw_results is None if return_raw_results=False
        """
        if include is None:
            include = ["statements", "chunks", "entities", "summaries"]

        # Use provided memory_config or fall back to instance config
        config = memory_config or self.memory_config
        if not config:
            raise ValueError("memory_config is required for search - either pass it to __init__ or execute_hybrid_search")

        # Clean query
        cleaned_query = self.clean_query(question)

        try:
            # Execute search using memory_config
            answer = await run_hybrid_search(
                query_text=cleaned_query,
                search_type=search_type,
                group_id=group_id,
                limit=limit,
                include=include,
                output_path=output_path,
                memory_config=config,
                rerank_alpha=rerank_alpha,
            )
            
            # Extract results based on search type and include parameter
            # Prioritize summaries as they contain synthesized contextual information
            answer_list = []
            
            # For hybrid search, use reranked_results
            if search_type == "hybrid":
                reranked_results = answer.get('reranked_results', {})
                
                # Priority order: summaries first (most contextual), then statements, chunks, entities
                priority_order = ['summaries', 'statements', 'chunks', 'entities']
                
                for category in priority_order:
                    if category in include and category in reranked_results:
                        category_results = reranked_results[category]
                        if isinstance(category_results, list):
                            answer_list.extend(category_results)
            else:
                # For keyword or embedding search, results are directly in answer dict
                # Apply same priority order
                priority_order = ['summaries', 'statements', 'chunks', 'entities']
                
                for category in priority_order:
                    if category in include and category in answer:
                        category_results = answer[category]
                        if isinstance(category_results, list):
                            answer_list.extend(category_results)
            
            # Extract clean content from all results
            content_list = [
                self.extract_content_from_result(ans) 
                for ans in answer_list
            ]

            
            # Filter out empty strings and join with newlines
            clean_content = '\n'.join([c for c in content_list if c])
            
            # Log first 200 chars
            logger.info(f"检索接口搜索结果==>>:{clean_content[:200]}...")
            
            # Return raw results if requested
            if return_raw_results:
                return clean_content, cleaned_query, answer
            else:
                return clean_content, cleaned_query, None
            
        except Exception as e:
            logger.error(
                f"Search failed for query '{question}' in group '{group_id}': {e}",
                exc_info=True
            )
            # Return empty results on failure
            if return_raw_results:
                return "", cleaned_query, {}
            else:
                return "", cleaned_query, None
