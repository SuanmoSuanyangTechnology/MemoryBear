import argparse
import asyncio
import json
import math
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig

from app.core.logging_config import get_memory_logger
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.models.config_models import TemporalSearchParams
from app.core.memory.models.variate_config import ForgettingEngineConfig
from app.core.memory.storage_services.forgetting_engine.forgetting_engine import (
    ForgettingEngine,
)
from app.core.memory.utils.config.config_utils import (
    get_pipeline_config,
)
from app.core.memory.utils.data.text_utils import extract_plain_query
from app.core.memory.utils.data.time_utils import normalize_date_safe
from app.core.memory.utils.llm.llm_utils import get_reranker_client
from app.core.models.base import RedBearModelConfig
from app.db import get_db_context
from app.repositories.neo4j.graph_search import (
    search_graph,
    search_graph_by_chunk_id,
    search_graph_by_embedding,
    search_graph_by_keyword_temporal,
    search_graph_by_temporal,
)

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.memory_config_service import MemoryConfigService
from dotenv import load_dotenv

load_dotenv()

logger = get_memory_logger(__name__)

def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse ISO `created_at` strings of the form 'YYYY-MM-DDTHH:MM:SS.ssssss'."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None
    return None


def normalize_scores(results: List[Dict[str, Any]], score_field: str = "score") -> List[Dict[str, Any]]:
    """Normalize scores using z-score normalization followed by sigmoid transformation."""
    if not results:
        return results

    # Extract scores, ensuring they are numeric and not None
    scores = []
    for item in results:
        if score_field in item:
            score = item.get(score_field)
            if score is not None and isinstance(score, (int, float)):
                scores.append(float(score))
            else:
                scores.append(0.0)  # Default for None or non-numeric values

    if not scores:
        return results

    if len(scores) == 1:
        # Single score, set to 1.0
        for item in results:
            if score_field in item:
                item[f"normalized_{score_field}"] = 1.0
        return results

    # Calculate mean and standard deviation
    mean_score = sum(scores) / len(scores)
    variance = sum((score - mean_score) ** 2 for score in scores) / len(scores)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        # All scores are the same, set them to 1.0
        for item in results:
            if score_field in item:
                item[f"normalized_{score_field}"] = 1.0
    else:
        for item in results:
            if score_field in item:
                score = item[score_field]
                # Handle None or non-numeric scores
                if score is None or not isinstance(score, (int, float)):
                    score = 0.0
                # Calculate z-score
                z_score = (score - mean_score) / std_dev
                # Transform to positive range using sigmoid function
                normalized = 1 / (1 + math.exp(-z_score))
                item[f"normalized_{score_field}"] = normalized

    return results


def rerank_hybrid_results(
    keyword_results: Dict[str, List[Dict[str, Any]]],
    embedding_results: Dict[str, List[Dict[str, Any]]],
    alpha: float = 0.6,
    limit: int = 10
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Rerank hybrid search results by combining BM25 and embedding scores.

    Args:
        keyword_results: Results from keyword/BM25 search
        embedding_results: Results from embedding search
        alpha: Weight for BM25 scores (1-alpha for embedding scores)
        limit: Maximum number of results to return per category

    Returns:
        Reranked results with combined scores
    """
    reranked = {}

    for category in ["statements", "chunks", "entities","summaries"]:
        keyword_items = keyword_results.get(category, [])
        embedding_items = embedding_results.get(category, [])

        # Normalize scores within each search type
        keyword_items = normalize_scores(keyword_items, "score")
        embedding_items = normalize_scores(embedding_items, "score")

        # Create a combined pool of unique items
        combined_items = {}

        # Add keyword results with BM25 scores
        for item in keyword_items:
            item_id = item.get("id") or item.get("uuid") or item.get("chunk_id")
            if item_id:
                combined_items[item_id] = item.copy()
                combined_items[item_id]["bm25_score"] = item.get("normalized_score", 0)
                combined_items[item_id]["embedding_score"] = 0  # Default

        # Add or update with embedding results
        for item in embedding_items:
            item_id = item.get("id") or item.get("uuid") or item.get("chunk_id")
            if item_id:
                if item_id in combined_items:
                    # Update existing item with embedding score
                    combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)
                else:
                    # New item from embedding search only
                    combined_items[item_id] = item.copy()
                    combined_items[item_id]["bm25_score"] = 0  # Default
                    combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)

        # Calculate combined scores and rank
        for item_id, item in combined_items.items():
            bm25_score = item.get("bm25_score", 0)
            embedding_score = item.get("embedding_score", 0)

            # Combined score: weighted average of normalized scores
            combined_score = alpha * bm25_score + (1 - alpha) * embedding_score
            item["combined_score"] = combined_score

            # Keep original score for reference
            if "score" not in item and bm25_score > 0:
                item["score"] = bm25_score
            elif "score" not in item and embedding_score > 0:
                item["score"] = embedding_score

        # Sort by combined score and limit results
        sorted_items = sorted(
            combined_items.values(),
            key=lambda x: x.get("combined_score", 0),
            reverse=True
        )[:limit]

        reranked[category] = sorted_items

    return reranked

def rerank_with_forgetting_curve(
    keyword_results: Dict[str, List[Dict[str, Any]]],
    embedding_results: Dict[str, List[Dict[str, Any]]],
    alpha: float = 0.6,
    limit: int = 10,
    forgetting_config: ForgettingEngineConfig | None = None,
    now: datetime | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Rerank hybrid results with a forgetting curve applied to combined scores.

    The forgetting curve reduces scores for older memories or weaker connections.

    Args:
        keyword_results: Results from keyword/BM25 search
        embedding_results: Results from embedding search
        alpha: Weight for BM25 scores (1-alpha for embedding scores)
        limit: Maximum number of results to return per category
        forgetting_config: Configuration for the forgetting engine
        now: Optional current time override for testing

    Returns:
        Reranked results with combined and final scores (after forgetting)
    """
    engine = ForgettingEngine(forgetting_config or ForgettingEngineConfig())
    now_dt = now or datetime.now()

    reranked: Dict[str, List[Dict[str, Any]]] = {}

    for category in ["statements", "chunks", "entities","summaries"]:
        keyword_items = keyword_results.get(category, [])
        embedding_items = embedding_results.get(category, [])

        # Normalize scores within each search type
        keyword_items = normalize_scores(keyword_items, "score")
        embedding_items = normalize_scores(embedding_items, "score")

        combined_items: Dict[str, Dict[str, Any]] = {}

        # Combine two result sets by ID
        for src_items, is_embedding in (
            (keyword_items, False), (embedding_items, True)
        ):
            for item in src_items:
                item_id = item.get("id") or item.get("uuid") or item.get("chunk_id")
                if not item_id:
                    continue
                existing = combined_items.get(item_id)
                if not existing:
                    combined_items[item_id] = item.copy()
                    combined_items[item_id]["bm25_score"] = 0
                    combined_items[item_id]["embedding_score"] = 0
                # Update normalized score from the right source
                if is_embedding:
                    combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)
                else:
                    combined_items[item_id]["bm25_score"] = item.get("normalized_score", 0)

        # Calculate scores and apply forgetting weights
        for item_id, item in combined_items.items():
            bm25_score = float(item.get("bm25_score", 0) or 0)
            embedding_score = float(item.get("embedding_score", 0) or 0)
            combined_score = alpha * bm25_score + (1 - alpha) * embedding_score

            # Estimate time elapsed in days
            dt = _parse_datetime(item.get("created_at"))
            if dt is None:
                time_elapsed_days = 0.0
            else:
                time_elapsed_days = max(0.0, (now_dt - dt).total_seconds() / 86400.0)

            # Memory strength (currently set to default value)
            memory_strength = 1.0
            forgetting_weight = engine.calculate_weight(
                time_elapsed=time_elapsed_days, memory_strength=memory_strength
            )
            # print(f"Forgetting weight for {item_id}: {forgetting_weight}")
            # print(f"Time elapsed days for {item_id}: {time_elapsed_days}")
            final_score = combined_score * forgetting_weight
            item["combined_score"] = final_score

        sorted_items = sorted(
            combined_items.values(), key=lambda x: x.get("combined_score", 0), reverse=True
        )[:limit]

        reranked[category] = sorted_items

    return reranked


def log_search_query(query_text: str, search_type: str, group_id: str | None, limit: int, include: List[str], log_file: str = None):
    """Log search query information using the logger.
    
    Args:
        query_text: The search query text
        search_type: Type of search (keyword, embedding, hybrid)
        group_id: Group identifier for filtering
        limit: Maximum number of results
        include: List of result types to include
        log_file: Deprecated parameter, kept for backward compatibility
    """
    # Ensure the query text is plain and clean before logging
    cleaned_query = extract_plain_query(query_text)
    
    # Log using the standard logger
    logger.info(
        f"Search query: query='{cleaned_query}', type={search_type}, "
        f"group_id={group_id}, limit={limit}, include={include}"
    )


def _remove_keys_recursive(obj: Any, keys_to_remove: List[str]) -> Any:
    """Remove specified keys recursively from dict/list structures (in place)."""
    try:
        if isinstance(obj, dict):
            for k in keys_to_remove:
                if k in obj:
                    obj.pop(k, None)
            for v in list(obj.values()):
                _remove_keys_recursive(v, keys_to_remove)
        elif isinstance(obj, list):
            for item in obj:
                _remove_keys_recursive(item, keys_to_remove)
    except Exception:
        # Be defensive: never fail search because of sanitization
        pass
    return obj


def apply_reranker_placeholder(
    results: Dict[str, List[Dict[str, Any]]],
    query_text: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Placeholder for a cross-encoder reranker.
    If config enables reranker, annotate items with a final_score equal to combined_score
    and keep ordering. This is a no-op reranker to be replaced later.
    """
    # try:
    #     rc = (RUNTIME_CONFIG.get("reranker", {}) or CONFIG.get("reranker", {}))
    # except Exception as e:
    #     logger.debug(f"Failed to load reranker config: {e}")
    #     rc = {}
    # if not rc or not rc.get("enabled", False):
    #     return results

    # top_k = int(rc.get("top_k", 100))
    # model_name = rc.get("model", "placeholder")

    # for cat, items in results.items():
    #     head = items[:top_k]
    #     for it in head:
    #         base = float(it.get("combined_score", it.get("score", 0.0)) or 0.0)
    #         it["final_score"] = base
    #         it["reranker_model"] = model_name
    #     # Keep overall order by final_score if present, otherwise combined/score
    #     results[cat] = sorted(
    #         items,
    #         key=lambda x: float(x.get("final_score", x.get("combined_score", x.get("score", 0.0)) or 0.0)),
    #         reverse=True,
    #     )
    return results


# async def apply_llm_reranker(
#     results: Dict[str, List[Dict[str, Any]]],
#     query_text: str,
#     reranker_client: Optional[Any] = None,
#     llm_weight: Optional[float] = None,
#     top_k: Optional[int] = None,
#     batch_size: Optional[int] = None,
# ) -> Dict[str, List[Dict[str, Any]]]:
#     """
#     Apply LLM-based reranking to search results.
    
#     Args:
#         results: Search results organized by category
#         query_text: Original search query
#         reranker_client: Optional pre-initialized reranker client
#         llm_weight: Weight for LLM score (0.0-1.0, higher favors LLM)
#         top_k: Maximum number of items to rerank per category
#         batch_size: Number of items to process concurrently
        
#     Returns:
#         Reranked results with final_score and reranker_model fields
#     """
#     # Load reranker configuration from runtime.json
#     # try:
#     #     rc = RUNTIME_CONFIG.get("reranker", {}) or CONFIG.get("reranker", {})
#     # except Exception as e:
#     #     logger.debug(f"Failed to load reranker config: {e}")
#     #     rc = {}
    
#     # Check if reranking is enabled
#     enabled = rc.get("enabled", False)
#     if not enabled:
#         logger.debug("LLM reranking is disabled in configuration")
#         return results
    
#     # Load configuration parameters with defaults
#     llm_weight = llm_weight if llm_weight is not None else rc.get("llm_weight", 0.5)
#     top_k = top_k if top_k is not None else rc.get("top_k", 20)
#     batch_size = batch_size if batch_size is not None else rc.get("batch_size", 5)
    
#     # Initialize reranker client if not provided
#     if reranker_client is None:
#         try:
#             reranker_client = get_reranker_client()
#         except Exception as e:
#             logger.warning(f"Failed to initialize reranker client: {e}, skipping LLM reranking")
#             return results
    
#     # Get model name for metadata
#     model_name = getattr(reranker_client, 'model_name', 'unknown')
    
#     # Process each category
#     reranked_results = {}
#     for category in ["statements", "chunks", "entities", "summaries"]:
#         items = results.get(category, [])
#         if not items:
#             reranked_results[category] = []
#             continue
        
#         # Select top K items by combined_score for reranking
#         sorted_items = sorted(
#             items,
#             key=lambda x: float(x.get("combined_score", x.get("score", 0.0)) or 0.0),
#             reverse=True
#         )
        
#         top_items = sorted_items[:top_k]
#         remaining_items = sorted_items[top_k:]
        
#         # Extract text content from each item
#         def extract_text(item: Dict[str, Any]) -> str:
#             """Extract text content from a result item."""
#             # Try different text fields based on category
#             text = item.get("text") or item.get("content") or item.get("statement") or item.get("name") or ""
#             return str(text).strip()
        
#         # Batch items for concurrent processing
#         batches = []
#         for i in range(0, len(top_items), batch_size):
#             batch = top_items[i:i + batch_size]
#             batches.append(batch)
        
#         # Process batches concurrently
#         async def process_batch(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
#             """Process a batch of items with LLM relevance scoring."""
#             scored_batch = []
            
#             for item in batch:
#                 item_text = extract_text(item)
                
#                 # Skip items with no text
#                 if not item_text:
#                     item_copy = item.copy()
#                     combined_score = float(item.get("combined_score", item.get("score", 0.0)) or 0.0)
#                     item_copy["final_score"] = combined_score
#                     item_copy["llm_relevance_score"] = 0.0
#                     item_copy["reranker_model"] = model_name
#                     scored_batch.append(item_copy)
#                     continue
                
#                 # Create relevance scoring prompt
#                 prompt = f"""Given the search query and a result item, rate the relevance of the item to the query on a scale from 0.0 to 1.0.

# Query: {query_text}

# Result: {item_text}

# Respond with only a number between 0.0 and 1.0, where:
# - 0.0 means completely irrelevant
# - 1.0 means perfectly relevant

# Relevance score:"""
                
#                 # Send request to LLM
#                 try:
#                     messages = [{"role": "user", "content": prompt}]
#                     response = await reranker_client.chat(messages)
                    
#                     # Parse LLM response to extract relevance score
#                     response_text = str(response.content if hasattr(response, 'content') else response).strip()
                    
#                     # Try to extract a float from the response
#                     try:
#                         # Remove any non-numeric characters except decimal point
#                         import re
#                         score_match = re.search(r'(\d+\.?\d*)', response_text)
#                         if score_match:
#                             llm_score = float(score_match.group(1))
#                             # Clamp to [0.0, 1.0]
#                             llm_score = max(0.0, min(1.0, llm_score))
#                         else:
#                             raise ValueError("No numeric score found in response")
#                     except (ValueError, AttributeError) as e:
#                         logger.warning(f"Invalid LLM score format: {response_text}, using combined_score. Error: {e}")
#                         llm_score = None
                    
#                     # Calculate final score
#                     item_copy = item.copy()
#                     combined_score = float(item.get("combined_score", item.get("score", 0.0)) or 0.0)
                    
#                     if llm_score is not None:
#                         final_score = (1 - llm_weight) * combined_score + llm_weight * llm_score
#                         item_copy["llm_relevance_score"] = llm_score
#                     else:
#                         # Use combined_score as fallback
#                         final_score = combined_score
#                         item_copy["llm_relevance_score"] = combined_score
                    
#                     item_copy["final_score"] = final_score
#                     item_copy["reranker_model"] = model_name
#                     scored_batch.append(item_copy)
#                 except Exception as e:
#                     logger.warning(f"Error processing item in LLM reranking: {e}, using combined_score")
#                     item_copy = item.copy()
#                     combined_score = float(item.get("combined_score", item.get("score", 0.0)) or 0.0)
#                     item_copy["final_score"] = combined_score
#                     item_copy["llm_relevance_score"] = combined_score
#                     item_copy["reranker_model"] = model_name
#                     scored_batch.append(item_copy)
            
#             return scored_batch
        
#         # Process all batches concurrently
#         try:
#             batch_tasks = [process_batch(batch) for batch in batches]
#             batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            
#             # Merge batch results
#             scored_items = []
#             for result in batch_results:
#                 if isinstance(result, Exception):
#                     logger.warning(f"Batch processing failed: {result}")
#                     continue
#                 scored_items.extend(result)
            
#             # Add remaining items (not in top K) with their combined_score as final_score
#             for item in remaining_items:
#                 item_copy = item.copy()
#                 combined_score = float(item.get("combined_score", item.get("score", 0.0)) or 0.0)
#                 item_copy["final_score"] = combined_score
#                 item_copy["reranker_model"] = model_name
#                 scored_items.append(item_copy)
            
#             # Sort all items by final_score in descending order
#             scored_items.sort(key=lambda x: float(x.get("final_score", 0.0) or 0.0), reverse=True)
#             reranked_results[category] = scored_items
            
#         except Exception as e:
#             logger.error(f"Error in LLM reranking for category {category}: {e}, returning original results")
#             # Return original items with combined_score as final_score
#             for item in items:
#                 combined_score = float(item.get("combined_score", item.get("score", 0.0)) or 0.0)
#                 item["final_score"] = combined_score
#                 item["reranker_model"] = model_name
#             reranked_results[category] = items
    
#     return reranked_results


async def run_hybrid_search(
    query_text: str,
    search_type: str,
    group_id: str | None,
    limit: int,
    include: List[str],
    output_path: str | None,
    memory_config: "MemoryConfig",
    rerank_alpha: float = 0.6,
    use_forgetting_rerank: bool = False,
    use_llm_rerank: bool = False,
):
    """

    Run search with specified type: 'keyword', 'embedding', or 'hybrid'
    
    Args:
        memory_config: MemoryConfig object containing embedding_model_id and config_id
    """
    # Start overall timing
    search_start_time = time.time()
    latency_metrics = {}
    logger.info(f"using embedding_id:{memory_config.embedding_model_id}...")

    # Clean and normalize the incoming query before use/logging
    query_text = extract_plain_query(query_text)
    
    # Validate query is not empty after cleaning
    if not query_text or not query_text.strip():
        logger.warning("Empty query after cleaning, returning empty results")
        return {
            "keyword_search": {},
            "embedding_search": {},
            "reranked_results": {},
            "combined_summary": {
                "total_keyword_results": 0,
                "total_embedding_results": 0,
                "total_reranked_results": 0,
                "search_query": "",
                "search_timestamp": datetime.now().isoformat(),
                "error": "Empty query"
            }
        }
    
    # Log the search query
    log_search_query(query_text, search_type, group_id, limit, include)

    connector = Neo4jConnector()
    results = {}

    try:
        keyword_task = None
        embedding_task = None

        if search_type in ["keyword", "hybrid"]:
            # Keyword-based search
            logger.info("Starting keyword search...")
            keyword_start = time.time()
            keyword_task = asyncio.create_task(
                search_graph(
                    connector=connector,
                    q=query_text,
                    group_id=group_id,
                    limit=limit,
                    include=include
                )
            )

        if search_type in ["embedding", "hybrid"]:
            # Embedding-based search
            logger.info("Starting embedding search...")
            embedding_start = time.time()
            
            # 从数据库读取嵌入器配置（按 ID）并构建 RedBearModelConfig
            config_load_start = time.time()
            with get_db_context() as db:
                config_service = MemoryConfigService(db)
                embedder_config_dict = config_service.get_embedder_config(str(memory_config.embedding_model_id))
            rb_config = RedBearModelConfig(
                model_name=embedder_config_dict["model_name"],
                provider=embedder_config_dict["provider"],
                api_key=embedder_config_dict["api_key"],
                base_url=embedder_config_dict["base_url"],
                type="llm"
            )
            config_load_time = time.time() - config_load_start
            logger.info(f"Config loading took {config_load_time:.4f}s")

            # Init embedder
            embedder_init_start = time.time()
            embedder = OpenAIEmbedderClient(model_config=rb_config)
            embedder_init_time = time.time() - embedder_init_start
            logger.info(f"Embedder init took {embedder_init_time:.4f}s")
            
            embedding_task = asyncio.create_task(
                search_graph_by_embedding(
                    connector=connector,
                    embedder_client=embedder,
                    query_text=query_text,
                    group_id=group_id,
                    limit=limit,
                    include=include,
                )
            )

        if keyword_task:
            keyword_results = await keyword_task
            keyword_latency = time.time() - keyword_start
            latency_metrics["keyword_search_latency"] = round(keyword_latency, 4)
            logger.info(f"Keyword search completed in {keyword_latency:.4f}s")
            if search_type == "keyword":
                results = keyword_results
            else:
                results["keyword_search"] = keyword_results

        if embedding_task:
            embedding_results = await embedding_task
            embedding_latency = time.time() - embedding_start
            latency_metrics["embedding_search_latency"] = round(embedding_latency, 4)
            logger.info(f"Embedding search completed in {embedding_latency:.4f}s")
            if search_type == "embedding":
                results = embedding_results
            else:
                results["embedding_search"] = embedding_results

        # Merge and rank results for hybrid search
        if search_type == "hybrid":
            results["combined_summary"] = {
                "total_keyword_results": sum(len(v) if isinstance(v, list) else 0 for v in keyword_results.values()),
                "total_embedding_results": sum(len(v) if isinstance(v, list) else 0 for v in embedding_results.values()),
                "search_query": query_text,
                "search_timestamp": datetime.now().isoformat()
            }

            # Apply reranking (optionally with forgetting curve)
            rerank_start = time.time()
            if use_forgetting_rerank:
                # Load forgetting parameters from pipeline config
                try:
                    pc = get_pipeline_config(memory_config)
                    forgetting_cfg = pc.forgetting_engine
                except Exception as e:
                    logger.debug(f"Failed to load forgetting config, using defaults: {e}")
                    forgetting_cfg = ForgettingEngineConfig()
                reranked_results = rerank_with_forgetting_curve(
                    keyword_results=keyword_results,
                    embedding_results=embedding_results,
                    alpha=rerank_alpha,
                    limit=limit,
                    forgetting_config=forgetting_cfg,
                )
            else:
                reranked_results = rerank_hybrid_results(
                    keyword_results=keyword_results,
                    embedding_results=embedding_results,
                    alpha=rerank_alpha,  # Configurable weight for BM25 vs embedding
                    limit=limit
                )
            rerank_latency = time.time() - rerank_start
            latency_metrics["reranking_latency"] = round(rerank_latency, 4)
            logger.info(f"Reranking completed in {rerank_latency:.4f}s")
            
            # Optional: apply reranker placeholder if enabled via config
            reranked_results = apply_reranker_placeholder(reranked_results, query_text)
            
            # Apply LLM reranking if enabled
            llm_rerank_applied = False
            # if use_llm_rerank:
            #     try:
            #         reranked_results = await apply_llm_reranker(
            #             results=reranked_results,
            #             query_text=query_text,
            #         )
            #         llm_rerank_applied = True
            #         logger.info("LLM reranking applied successfully")
            #     except Exception as e:
            #         logger.warning(f"LLM reranking failed: {e}, using previous scores")
            
            results["reranked_results"] = reranked_results
            results["combined_summary"] = {
                "total_keyword_results": sum(len(v) if isinstance(v, list) else 0 for v in keyword_results.values()),
                "total_embedding_results": sum(len(v) if isinstance(v, list) else 0 for v in embedding_results.values()),
                "total_reranked_results": sum(len(v) if isinstance(v, list) else 0 for v in reranked_results.values()),
                "search_query": query_text,
                "search_timestamp": datetime.now().isoformat(),
                "reranking_alpha": rerank_alpha,
                "forgetting_rerank": use_forgetting_rerank,
                "llm_rerank": llm_rerank_applied,
            }

        # Calculate total latency
        total_latency = time.time() - search_start_time
        latency_metrics["total_latency"] = round(total_latency, 4)
        
        # Add latency metrics to results
        if "combined_summary" in results:
            results["combined_summary"]["latency_metrics"] = latency_metrics
        else:
            results["latency_metrics"] = latency_metrics
        
        logger.info(f"Total search completed in {total_latency:.4f}s")
        logger.info(f"Latency breakdown: {latency_metrics}")

        # Sanitize results: drop large/unused fields
        _remove_keys_recursive(results, ["name_embedding"])  # drop entity name embeddings from outputs

        # print(json.dumps(results, ensure_ascii=False, indent=2, default=str))

        # Save to file
        output_path = output_path or "search_results.json"
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"Search results saved to: {output_path}")

        # Log search completion with result count
        if search_type == "hybrid":
            result_counts = {
                "keyword": {key: len(value) if isinstance(value, list) else 0 for key, value in keyword_results.items()},
                "embedding": {key: len(value) if isinstance(value, list) else 0 for key, value in embedding_results.items()}
            }
        else:
            result_counts = {key: len(value) if isinstance(value, list) else 0 for key, value in results.items()}

        # Log completion using the standard logger
        logger.info(
            f"Search completed: query='{query_text}', type={search_type}, "
            f"result_counts={result_counts}, latency={latency_metrics}"
        )

        return results

    finally:
        await connector.close()


async def search_by_temporal(
    group_id: Optional[str] = "test",
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    valid_date: Optional[str] = None,
    invalid_date: Optional[str] = None,
    limit: int = 1,
):
    """
    Temporal search across Statements.

    - Matches statements created between start_date and end_date
    - Optionally filters by group_id
    - Returns up to 'limit' statements
    """
    connector = Neo4jConnector()
    if start_date:
        start_date = normalize_date_safe(start_date)
    if end_date:
        end_date = normalize_date_safe(end_date)

    params = TemporalSearchParams.model_validate({
        "group_id": group_id,
        "apply_id": apply_id,
        "user_id": user_id,
        "start_date": start_date,
        "end_date": end_date,
        "valid_date": valid_date,
        "invalid_date": invalid_date,
        "limit": limit,
    })
    statements = await search_graph_by_temporal(
        connector=connector,
        group_id=params.group_id,
        apply_id=params.apply_id,
        user_id=params.user_id,
        start_date=params.start_date,
        end_date=params.end_date,
        valid_date=params.valid_date,
        invalid_date=params.invalid_date,
        limit=params.limit
    )
    return {"statements": statements}


async def search_by_keyword_temporal(
    query_text: str,
    group_id: Optional[str] = "test",
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    valid_date: Optional[str] = None,
    invalid_date: Optional[str] = None,
    limit: int = 1,
):
    """
    Temporal keyword search across Statements.
    """
    connector = Neo4jConnector()
    if start_date:
        start_date = normalize_date_safe(start_date)
    if end_date:
        end_date = normalize_date_safe(end_date)
    if valid_date:
        valid_date = normalize_date_safe(valid_date)
    if invalid_date:
        invalid_date = normalize_date_safe(invalid_date)

    params = TemporalSearchParams.model_validate({
        "group_id": group_id,
        "apply_id": apply_id,
        "user_id": user_id,
        "start_date": start_date,
        "end_date": end_date,
        "valid_date": valid_date,
        "invalid_date": invalid_date,
        "limit": limit,
    })
    statements = await search_graph_by_keyword_temporal(
        connector=connector,
        query_text=query_text,
        group_id=params.group_id,
        apply_id=params.apply_id,
        user_id=params.user_id,
        start_date=params.start_date,
        end_date=params.end_date,
        valid_date=params.valid_date,
        invalid_date=params.invalid_date,
        limit=params.limit
    )
    return {"statements": statements}


async def search_chunk_by_chunk_id(
    chunk_id: str,
    group_id: Optional[str] = "test",
    limit: int = 1,
):
    """
    Search for Chunks by chunk_id.
    """
    connector = Neo4jConnector()
    chunks = await search_graph_by_chunk_id(
        connector=connector,
        chunk_id=chunk_id,
        group_id=group_id,
        limit=limit
    )
    return {"chunks": chunks}


# def main():
#     """Main entry point for the hybrid graph search CLI.

#     Parses command line arguments and executes search with specified parameters.
#     Supports keyword, embedding, and hybrid search modes.
#     """
#     parser = argparse.ArgumentParser(description="Hybrid graph search with keyword and embedding options")
#     parser.add_argument(
#         "--query", "-q", required=True, help="Free-text query to search"
#     )
#     parser.add_argument(
#         "--search-type",
#         "-t",
#         choices=["keyword", "embedding", "hybrid"],
#         default="hybrid",
#         help="Search type: keyword (text matching), embedding (semantic), or hybrid (both) (default: hybrid)"
#     )
#     parser.add_argument(
#         "--config-id",
#         "-c",
#         type=int,
#         required=True,
#         help="Database configuration ID (required)",
#     )
#     parser.add_argument(
#         "--group-id",
#         "-g",
#         default=None,
#         help="Optional group_id to filter results (default: None)",
#     )
#     parser.add_argument(
#         "--limit",
#         "-k",
#         type=int,
#         default=5,
#         help="Max number of results per type (default: 5)",
#     )
#     parser.add_argument(
#         "--include",
#         "-i",
#         nargs="+",
#         default=["statements", "chunks", "entities", "summaries"],
#         choices=["statements", "chunks", "entities", "summaries"],
#         help="Which targets to search for embedding search (default: statements chunks entities summaries)"
#     )
#     parser.add_argument(
#         "--output",
#         "-o",
#         default="search_results.json",
#         help="Path to save the search results JSON (default: search_results.json)",
#     )
#     parser.add_argument(
#         "--rerank-alpha",
#         "-a",
#         type=float,
#         default=0.6,
#         help="Weight for BM25 scores in reranking (0.0-1.0, higher values favor keyword search) (default: 0.6)",
#     )
#     parser.add_argument(
#         "--forgetting-rerank",
#         action="store_true",
#         help="Apply forgetting curve during reranking for hybrid search.",
#     )
#     parser.add_argument(
#         "--llm-rerank",
#         action="store_true",
#         help="Apply LLM-based reranking for hybrid search.",
#     )
#     args = parser.parse_args()

#     # Load memory config from database
#     from app.services.memory_config_service import MemoryConfigService
#     memory_config = MemoryConfigService.load_memory_config(args.config_id)

#     asyncio.run(
#         run_hybrid_search(
#             query_text=args.query,
#             search_type=args.search_type,
#             group_id=args.group_id,
#             limit=args.limit,
#             include=args.include,
#             output_path=args.output,
#             memory_config=memory_config,
#             rerank_alpha=args.rerank_alpha,
#             use_forgetting_rerank=args.forgetting_rerank,
#             use_llm_rerank=args.llm_rerank,
#         )
#     )


# if __name__ == "__main__":
#     main()
