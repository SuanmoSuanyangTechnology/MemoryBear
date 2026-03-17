"""
Search Service for executing hybrid search and processing results.

This service provides clean search result processing with content extraction
and deduplication.
"""
from typing import List, Tuple, Optional

from app.core.logging_config import get_agent_logger
from app.core.memory.src.search import run_hybrid_search
from app.core.memory.utils.data.text_utils import escape_lucene_query


logger = get_agent_logger(__name__)


class SearchService:
    """Service for executing hybrid search and processing results."""
    
    def __init__(self):
        """Initialize the search service."""
        logger.info("SearchService initialized")
    
    def extract_content_from_result(self, result: dict, node_type: str = "") -> str:
        """
        Extract only meaningful content from search results, dropping all metadata.
        
        Extraction rules by node type:
        - Statements: extract 'statement' field
        - Entities: extract 'name' and 'fact_summary' fields
        - Summaries: extract 'content' field
        - Chunks: extract 'content' field
        - Communities: extract 'content' field (c.summary), prefixed with community name
        
        Args:
            result: Search result dictionary
            node_type: Hint for node type ("community", "summary", etc.)
            
        Returns:
            Clean content string without metadata
        """
        if not isinstance(result, dict):
            return str(result)
        
        content_parts = []
        
        # Statements: extract statement field
        if 'statement' in result and result['statement']:
            content_parts.append(result['statement'])
        
        # Community 节点：有 member_count 或 core_entities 字段，或 node_type 明确指定
        # 用 "[主题：{name}]" 前缀区分，让 LLM 知道这是主题级摘要
        is_community = (
            node_type == "community"
            or 'member_count' in result
            or 'core_entities' in result
        )
        if is_community:
            name = result.get('name', '')
            content = result.get('content', '')
            if content:
                prefix = f"[主题：{name}] " if name else ""
                content_parts.append(f"{prefix}{content}")
        elif 'content' in result and result['content']:
            # Summaries / Chunks
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
        end_user_id: str,
        question: str,
        limit: int = 5,
        search_type: str = "hybrid",
        include: Optional[List[str]] = None,
        rerank_alpha: float = 0.4,
        output_path: str = "search_results.json",
        return_raw_results: bool = False,
        memory_config = None,
        expand_communities: bool = True,
    ) -> Tuple[str, str, Optional[dict]]:
        """
        Execute hybrid search and return clean content.
        
        Args:
            end_user_id: Group identifier for filtering results
            question: Search query text
            limit: Maximum number of results to return (default: 5)
            search_type: Type of search - "hybrid", "keyword", or "embedding" (default: "hybrid")
            include: List of result types to include (default: ["statements", "chunks", "entities", "summaries"])
            rerank_alpha: Weight for BM25 scores in reranking (default: 0.4)
            output_path: Path to save search results (default: "search_results.json")
            return_raw_results: If True, also return the raw search results as third element (default: False)
            memory_config: Memory configuration object (required)
            expand_communities: If True, expand community hits to member statements (default: True).
                                 Set to False for quick-summary paths that only need community-level text.
        
        Returns:
            Tuple of (clean_content, cleaned_query, raw_results)
            raw_results is None if return_raw_results=False
        """
        if include is None:
            include = ["statements", "chunks", "entities", "summaries", "communities"]
        
        # Clean query
        cleaned_query = self.clean_query(question)
        
        try:
            # Execute search
            answer = await run_hybrid_search(
                query_text=cleaned_query,
                search_type=search_type,
                end_user_id=end_user_id,
                limit=limit,
                include=include,
                output_path=output_path,
                memory_config=memory_config,
                rerank_alpha=rerank_alpha
            )
            
            # Extract results based on search type and include parameter
            # Prioritize summaries as they contain synthesized contextual information
            answer_list = []
            
            # For hybrid search, use reranked_results
            if search_type == "hybrid":
                reranked_results = answer.get('reranked_results', {})
                
                # Priority order: summaries first (most contextual), then communities, statements, chunks, entities
                priority_order = ['summaries', 'communities', 'statements', 'chunks', 'entities']
                
                for category in priority_order:
                    if category in include and category in reranked_results:
                        category_results = reranked_results[category]
                        if isinstance(category_results, list):
                            answer_list.extend(category_results)
            else:
                # For keyword or embedding search, results are directly in answer dict
                # Apply same priority order
                priority_order = ['summaries', 'communities', 'statements', 'chunks', 'entities']
                
                for category in priority_order:
                    if category in include and category in answer:
                        category_results = answer[category]
                        if isinstance(category_results, list):
                            answer_list.extend(category_results)

            # 对命中的 community 节点展开其成员 statements（路径 "0"/"1" 需要，路径 "2" 不需要）
            if expand_communities and "communities" in include:
                community_results = (
                    answer.get('reranked_results', {}).get('communities', [])
                    if search_type == "hybrid"
                    else answer.get('communities', [])
                )
                community_ids = [
                    r.get("id") for r in community_results if r.get("id")
                ]
                if community_ids and end_user_id:
                    from app.repositories.neo4j.graph_search import search_graph_community_expand
                    from app.repositories.neo4j.neo4j_connector import Neo4jConnector
                    expand_connector = Neo4jConnector()
                    try:
                        expand_result = await search_graph_community_expand(
                            connector=expand_connector,
                            community_ids=community_ids,
                            end_user_id=end_user_id,
                            limit=10,
                        )
                        expanded_stmts = expand_result.get("expanded_statements", [])
                        if expanded_stmts:
                            answer_list.extend(expanded_stmts)
                            logger.info(f"社区展开检索追加 {len(expanded_stmts)} 条 statements")
                    except Exception as e:
                        logger.warning(f"社区展开检索失败，跳过: {e}")
                    finally:
                        await expand_connector.close()
            
            # Extract clean content from all results，按类型传入 node_type 区分 community
            content_list = []
            for ans in answer_list:
                # community 节点有 member_count 或 core_entities 字段
                ntype = "community" if ('member_count' in ans or 'core_entities' in ans) else ""
                content_list.append(self.extract_content_from_result(ans, node_type=ntype))

            
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
                f"Search failed for query '{question}' in group '{end_user_id}': {e}",
                exc_info=True
            )
            # Return empty results on failure
            if return_raw_results:
                return "", cleaned_query, {}
            else:
                return "", cleaned_query, None
