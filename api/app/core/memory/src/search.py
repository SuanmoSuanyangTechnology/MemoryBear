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
            # 对于 activation_value，None 值保持为 None，不使用回退值
            # 这样可以区分有激活值和无激活值的节点
            if score_field == "activation_value" and score is None:
                scores.append(None)  # 保持 None，稍后特殊处理
                continue
            
            if score is not None and isinstance(score, (int, float)):
                scores.append(float(score))
            else:
                scores.append(0.0)  # Default for None or non-numeric values

    if not scores:
        return results
    
    # 过滤掉 None 值，只对有效分数进行归一化
    valid_scores = [s for s in scores if s is not None]
    
    if not valid_scores:
        # 所有分数都是 None，不进行归一化
        for item in results:
            if score_field in item or score_field == "activation_value":
                item[f"normalized_{score_field}"] = None
        return results

    if len(valid_scores) == 1:        # Single valid score, set to 1.0
        for item, score in zip(results, scores):
            if score_field in item or score_field == "activation_value":
                if score is None:
                    item[f"normalized_{score_field}"] = None
                else:
                    item[f"normalized_{score_field}"] = 1.0
        return results

    # Calculate mean and standard deviation (only for valid scores)
    mean_score = sum(valid_scores) / len(valid_scores)
    variance = sum((score - mean_score) ** 2 for score in valid_scores) / len(valid_scores)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        # All valid scores are the same, set them to 1.0
        for item, score in zip(results, scores):
            if score_field in item or score_field == "activation_value":
                if score is None:
                    item[f"normalized_{score_field}"] = None
                else:
                    item[f"normalized_{score_field}"] = 1.0
    else:
        for item, score in zip(results, scores):
            if score_field in item or score_field == "activation_value":
                if score is None:
                    # 保持 None，不进行归一化
                    item[f"normalized_{score_field}"] = None
                else:
                    # Calculate z-score
                    z_score = (score - mean_score) / std_dev
                    # Transform to positive range using sigmoid function
                    normalized = 1 / (1 + math.exp(-z_score))
                    item[f"normalized_{score_field}"] = normalized

    return results



def _deduplicate_results(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove duplicate items from search results based on content.
    
    Deduplication strategy:
    1. First try to deduplicate by ID (id, uuid, or chunk_id)
    2. Then deduplicate by content hash (text, content, statement, or name fields)
    
    Args:
        items: List of search result items
        
    Returns:
        Deduplicated list of items, preserving the order of first occurrence
    """
    seen_ids = set()
    seen_content = set()
    deduplicated = []
    
    for item in items:
        # Try multiple ID fields to identify unique items
        item_id = item.get("id") or item.get("uuid") or item.get("chunk_id")
        
        # Extract content from various possible fields
        content = (
            item.get("text") or 
            item.get("content") or 
            item.get("statement") or 
            item.get("name") or 
            ""
        )
        
        # Normalize content for comparison (strip whitespace and lowercase)
        normalized_content = str(content).strip().lower() if content else ""
        
        # Check if we've seen this ID or content before
        is_duplicate = False
        
        if item_id and item_id in seen_ids:
            is_duplicate = True
        elif normalized_content and normalized_content in seen_content:
            # Only check content duplication if content is not empty
            is_duplicate = True
        
        if not is_duplicate:
            # Mark as seen
            if item_id:
                seen_ids.add(item_id)
            if normalized_content:  # Only track non-empty content
                seen_content.add(normalized_content)
            
            deduplicated.append(item)
    
    return deduplicated


def rerank_with_activation(
    keyword_results: Dict[str, List[Dict[str, Any]]],
    embedding_results: Dict[str, List[Dict[str, Any]]],
    alpha: float = 0.6,
    limit: int = 10,
    forgetting_config: ForgettingEngineConfig | None = None,
    activation_boost_factor: float = 0.8,
    now: datetime | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    两阶段排序：先按内容相关性筛选，再按激活值排序。
    
    阶段1: content_score = alpha*BM25 + (1-alpha)*Embedding，取 Top-(limit*3)
    阶段2: 在候选中按 activation_score 排序，取 Top-limit
           无激活值的节点用于补充不足
    
    返回结果中的评分字段说明：
        - bm25_score: BM25 归一化分数
        - embedding_score: Embedding 归一化分数
        - content_score: 内容相关性 = alpha*bm25 + (1-alpha)*embedding
        - activation_score: ACTR 激活值归一化分数
        - base_score: 第一阶段基础分数（等于 content_score）
        - final_score: 最终排序依据
            * 有激活值的节点：final_score = activation_score
            * 无激活值的节点：final_score = base_score
    
    参数:
        keyword_results: BM25 检索结果
        embedding_results: 向量嵌入检索结果
        alpha: BM25 权重 (默认: 0.6)
        limit: 每类最大结果数
        forgetting_config: 遗忘引擎配置（当前未使用）
        activation_boost_factor: 激活度对记忆强度的影响系数 (默认: 0.8)
        now: 当前时间（用于遗忘计算）
        
    返回:
        带评分元数据的重排序结果，按 final_score 排序
    """
    # 验证权重范围
    if not (0 <= alpha <= 1):
        raise ValueError(f"alpha 必须在 [0, 1] 范围内，当前值: {alpha}")
    
    # 初始化遗忘引擎（如果需要）
    engine = None
    if forgetting_config:
        engine = ForgettingEngine(forgetting_config)
    now_dt = now or datetime.now()
    
    reranked: Dict[str, List[Dict[str, Any]]] = {}
    
    for category in ["statements", "chunks", "entities", "summaries"]:
        keyword_items = keyword_results.get(category, [])
        embedding_items = embedding_results.get(category, [])
        
        # 步骤 1: 归一化分数
        keyword_items = normalize_scores(keyword_items, "score")
        embedding_items = normalize_scores(embedding_items, "score")
        
        # 步骤 2: 按 ID 合并结果（去重）
        combined_items: Dict[str, Dict[str, Any]] = {}
        
        # 添加关键词结果
        for item in keyword_items:
            item_id = item.get("id") or item.get("uuid") or item.get("chunk_id")
            if not item_id:
                continue
            combined_items[item_id] = item.copy()
            combined_items[item_id]["bm25_score"] = item.get("normalized_score", 0)
            combined_items[item_id]["embedding_score"] = 0  # 默认值
        
        # 添加或更新向量嵌入结果
        for item in embedding_items:
            item_id = item.get("id") or item.get("uuid") or item.get("chunk_id")
            if not item_id:
                continue
            if item_id in combined_items:
                # 更新现有项的嵌入分数
                combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)
            else:
                # 仅来自嵌入搜索的新项
                combined_items[item_id] = item.copy()
                combined_items[item_id]["bm25_score"] = 0  # 默认值
                combined_items[item_id]["embedding_score"] = item.get("normalized_score", 0)
        
        # 步骤 3: 归一化激活度分数
        # 为所有项准备激活度值列表
        items_list = list(combined_items.values())
        items_list = normalize_scores(items_list, "activation_value")
        
        # 更新 combined_items 中的归一化激活度分数
        for item in items_list:
            item_id = item.get("id") or item.get("uuid") or item.get("chunk_id")
            if item_id and item_id in combined_items:
                combined_items[item_id]["normalized_activation_value"] = item.get("normalized_activation_value", 0)
        
        # 步骤 4: 计算基础分数和最终分数
        for item_id, item in combined_items.items():
            bm25_norm = float(item.get("bm25_score", 0) or 0)
            emb_norm = float(item.get("embedding_score", 0) or 0)
            act_norm = float(item.get("normalized_activation_value", 0) or 0)
            
            # 第一阶段：只考虑内容相关性（BM25 + Embedding）
            # alpha 控制 BM25 权重，(1-alpha) 控制 Embedding 权重
            content_score = alpha * bm25_norm + (1 - alpha) * emb_norm
            base_score = content_score  # 第一阶段用内容分数
            
            # 存储激活度分数供第二阶段使用
            item["activation_score"] = act_norm
            item["content_score"] = content_score
            item["base_score"] = base_score
            
            # 步骤 5: 应用遗忘曲线（可选）
            if engine:
                # 计算受激活度影响的记忆强度
                importance = float(item.get("importance_score", 0.5) or 0.5)
                
                # 获取 activation_value
                activation_val = item.get("activation_value")
                
                # 只对有激活值的节点应用遗忘曲线
                if activation_val is not None and isinstance(activation_val, (int, float)):
                    activation_val = float(activation_val)
                    
                    # 计算记忆强度：importance_score × (1 + activation_value × boost_factor)
                    memory_strength = importance * (1 + activation_val * activation_boost_factor)
                    
                    # 计算经过的时间（天数）
                    dt = _parse_datetime(item.get("created_at"))
                    if dt is None:
                        time_elapsed_days = 0.0
                    else:
                        time_elapsed_days = max(0.0, (now_dt - dt).total_seconds() / 86400.0)
                    
                    # 获取遗忘权重
                    forgetting_weight = engine.calculate_weight(
                        time_elapsed=time_elapsed_days,
                        memory_strength=memory_strength
                    )
                    
                    # 应用到基础分数
                    item["forgetting_weight"] = forgetting_weight
                    item["final_score"] = base_score * forgetting_weight
                else:
                    # 无激活值的节点不应用遗忘曲线，保持原始分数
                    item["final_score"] = base_score
            else:
                # 不使用遗忘曲线
                item["final_score"] = base_score
        
        # 步骤 6: 两阶段排序和限制
        # 第一阶段：按内容相关性（base_score）排序，取 Top-K
        first_stage_limit = limit * 3  # 可配置，取3倍候选
        first_stage_sorted = sorted(
            combined_items.values(),
            key=lambda x: float(x.get("base_score", 0) or 0),  # 按内容分数排序
            reverse=True
        )[:first_stage_limit]
        
        # 第二阶段：分离有激活值和无激活值的节点
        items_with_activation = []
        items_without_activation = []
        
        for item in first_stage_sorted:
            activation_score = item.get("activation_score")
            # 检查是否有有效的激活值（不是 None）
            if activation_score is not None and isinstance(activation_score, (int, float)):
                items_with_activation.append(item)
            else:
                items_without_activation.append(item)
        
        # 优先按激活值排序有激活值的节点
        sorted_with_activation = sorted(
            items_with_activation,
            key=lambda x: float(x.get("activation_score", 0) or 0),
            reverse=True
        )
        
        # 如果有激活值的节点不足 limit，用无激活值的节点补充
        if len(sorted_with_activation) < limit:
            needed = limit - len(sorted_with_activation)
            # 无激活值的节点保持第一阶段的内容相关性排序
            sorted_items = sorted_with_activation + items_without_activation[:needed]
        else:
            sorted_items = sorted_with_activation[:limit]
        
        # 两阶段排序完成，更新 final_score 以反映实际排序依据
        # Stage 1: 按 content_score 筛选候选（已完成）
        # Stage 2: 按 activation_score 排序（已完成）
        # 
        # final_score 语义：反映节点在最终结果中的排序依据
        #   - 有激活值的节点：final_score = activation_score（第二阶段排序依据）
        #   - 无激活值的节点：final_score = base_score（保持内容相关性分数）
        for item in sorted_items:
            activation_score = item.get("activation_score")
            if activation_score is not None and isinstance(activation_score, (int, float)):
                # 有激活值：使用激活度作为最终分数
                item["final_score"] = activation_score
            else:
                # 无激活值：使用内容相关性分数
                item["final_score"] = item.get("base_score", 0)
        
        # 最终去重确保没有重复项
        sorted_items = _deduplicate_results(sorted_items)
        
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
    activation_boost_factor: float = 0.8,
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
            logger.info("[PERF] Starting keyword search...")
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
            logger.info("[PERF] Starting embedding search...")
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
            logger.info(f"[PERF] Config loading took {config_load_time:.4f}s")

            # Init embedder
            embedder_init_start = time.time()
            embedder = OpenAIEmbedderClient(model_config=rb_config)
            embedder_init_time = time.time() - embedder_init_start
            logger.info(f"[PERF] Embedder init took {embedder_init_time:.4f}s")
            
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
            logger.info(f"[PERF] Keyword search completed in {keyword_latency:.4f}s")
            if search_type == "keyword":
                results = keyword_results
            else:
                results["keyword_search"] = keyword_results

        if embedding_task:
            embedding_results = await embedding_task
            embedding_latency = time.time() - embedding_start
            latency_metrics["embedding_search_latency"] = round(embedding_latency, 4)
            logger.info(f"[PERF] Embedding search completed in {embedding_latency:.4f}s")
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

            # Apply two-stage reranking with ACTR activation calculation
            rerank_start = time.time()
            logger.info("[PERF] Using two-stage reranking with ACTR activation")
            
            # 加载遗忘引擎配置
            config_start = time.time()
            try:
                pc = get_pipeline_config(memory_config)
                forgetting_cfg = pc.forgetting_engine
            except Exception as e:
                logger.debug(f"Failed to load forgetting config, using defaults: {e}")
                forgetting_cfg = ForgettingEngineConfig()
            config_time = time.time() - config_start
            logger.info(f"[PERF] Forgetting config loading took {config_time:.4f}s")
            
            # 统一使用激活度重排序（两阶段：检索 + ACTR计算）
            rerank_compute_start = time.time()
            reranked_results = rerank_with_activation(
                keyword_results=keyword_results,
                embedding_results=embedding_results,
                alpha=rerank_alpha,
                limit=limit,
                forgetting_config=forgetting_cfg,
                activation_boost_factor=activation_boost_factor,
            )
            rerank_compute_time = time.time() - rerank_compute_start
            logger.info(f"[PERF] Rerank computation took {rerank_compute_time:.4f}s")
            
            rerank_latency = time.time() - rerank_start
            latency_metrics["reranking_latency"] = round(rerank_latency, 4)
            logger.info(f"[PERF] Total reranking completed in {rerank_latency:.4f}s")
            
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
                "activation_boost_factor": activation_boost_factor,
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
        
        logger.info(f"[PERF] ===== SEARCH PERFORMANCE SUMMARY =====")
        logger.info(f"[PERF] Total search completed in {total_latency:.4f}s")
        logger.info(f"[PERF] Latency breakdown: {json.dumps(latency_metrics, indent=2)}")
        logger.info(f"[PERF] =========================================")

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

