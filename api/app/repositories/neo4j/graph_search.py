import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Coroutine

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.llm_tools import OpenAIEmbedderClient
from app.core.memory.utils.data.text_utils import escape_lucene_query
from app.core.models import RedBearEmbeddings
from app.repositories.neo4j.cypher_queries import (
    EXPAND_COMMUNITY_STATEMENTS,
    SEARCH_CHUNK_BY_CHUNK_ID,
    SEARCH_DIALOGUE_BY_DIALOG_ID,
    SEARCH_ENTITIES_BY_NAME,
    SEARCH_STATEMENTS_BY_CREATED_AT,
    SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL,
    SEARCH_STATEMENTS_BY_TEMPORAL,
    SEARCH_STATEMENTS_BY_VALID_AT,
    SEARCH_STATEMENTS_G_CREATED_AT,
    SEARCH_STATEMENTS_G_VALID_AT,
    SEARCH_STATEMENTS_L_CREATED_AT,
    SEARCH_STATEMENTS_L_VALID_AT,
    SEARCH_PERCEPTUALS_BY_KEYWORD,
    FULLTEXT_QUERY_CYPHER_MAPPING,
    NODE_ID_QUERY_CYPHER_MAPPING,
    COSINE_SEARCH_CYPHER_MAPPING,
    SEARCH_USER_METADATA,
    SEARCH_ENITITES_BY_RELATIONSHIP,
    SEARCH_RELATION_BETWEEN_ENTITIES,
    SEARCH_RELATIONS_BETWEEN_ENTITY_PAIRS,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


async def _update_activation_values_batch(
        nodes: List[Dict[str, Any]],
        node_label: str,
        end_user_id: Optional[str] = None,
        max_retries: int = 3
) -> List[Dict[str, Any]]:
    """
    批量更新节点的激活值
    
    为提高性能，批量更新多个节点的访问历史和激活值。
    使用重试机制处理更新失败的情况。
    
    Args:
        connector: Neo4j连接器
        nodes: 节点列表，每个节点必须包含 'id' 字段
        node_label: 节点标签（Statement, ExtractedEntity, MemorySummary）
        end_user_id: 组ID（可选）
        max_retries: 最大重试次数
    
    Returns:
        List[Dict[str, Any]]: 成功更新的节点列表
    """
    async with Neo4jConnector() as connector:
        if not nodes:
            return []

        # 延迟导入以避免循环依赖
        from app.core.memory.storage_services.forgetting_engine.access_history_manager import (
            AccessHistoryManager,
        )
        from app.core.memory.storage_services.forgetting_engine.actr_calculator import (
            ACTRCalculator,
        )

        # 创建计算器和管理器实例
        actr_calculator = ACTRCalculator()
        access_manager = AccessHistoryManager(
            connector=connector,
            actr_calculator=actr_calculator,
            max_retries=max_retries
        )

        # 提取节点ID列表并去重（保持原始顺序）
        seen_ids = set()
        unique_node_ids = []
        for node in nodes:
            node_id = node.get('id')
            if node_id and node_id not in seen_ids:
                seen_ids.add(node_id)
                unique_node_ids.append(node_id)

        if not unique_node_ids:
            logger.warning("批量更新激活值：没有有效的节点ID")
            return nodes

        # 记录去重信息（仅针对具有有效 ID 的节点）
        id_nodes_count = sum(1 for n in nodes if n.get("id"))
        if len(unique_node_ids) < id_nodes_count:
            logger.info(
                f"批量更新激活值：检测到重复节点，具有有效ID的节点数量={id_nodes_count}, "
                f"去重后唯一ID数量={len(unique_node_ids)}"
            )

        # 批量记录访问
        try:
            updated_nodes = await access_manager.record_batch_access(
                node_ids=unique_node_ids,
                node_label=node_label,
                end_user_id=end_user_id
            )

            logger.debug(
                f"批量更新激活值成功: {node_label}, "
                f"更新数量={len(updated_nodes)}/{len(unique_node_ids)}"
            )

            return updated_nodes

        except Exception as e:
            logger.error(
                f"批量更新激活值失败: {node_label}, 错误: {str(e)}"
            )
            # 失败时返回原始节点列表
            return nodes


async def _update_search_results_activation(
        connector: Neo4jConnector,
        results: Dict[str, List[Dict[str, Any]]],
        end_user_id: Optional[str] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    更新搜索结果中所有知识节点的激活值
    
    对 Statement、ExtractedEntity、MemorySummary 节点进行批量激活值更新。
    ChunkNode 和 DialogueNode 不参与激活值更新（数据层隔离）。
    
    Args:
        connector: Neo4j连接器
        results: 搜索结果字典，包含不同类型节点的列表
        end_user_id: 组ID（可选）
    
    Returns:
        Dict[str, List[Dict[str, Any]]]: 更新后的搜索结果
    """
    # 定义需要更新激活值的节点类型
    knowledge_node_types = {
        'statements': 'Statement',
        'entities': 'ExtractedEntity',
        'summaries': 'MemorySummary',
        Neo4jNodeType.STATEMENT: Neo4jNodeType.STATEMENT.value,
        Neo4jNodeType.EXTRACTEDENTITY: Neo4jNodeType.EXTRACTEDENTITY.value,
        Neo4jNodeType.MEMORYSUMMARY: Neo4jNodeType.MEMORYSUMMARY.value,
    }

    # 并行更新所有类型的节点
    update_tasks = []
    update_keys = []

    for key, label in knowledge_node_types.items():
        if key in results and results[key]:
            update_tasks.append(
                _update_activation_values_batch(
                    nodes=results[key],
                    node_label=label,
                    end_user_id=end_user_id
                )
            )
            update_keys.append(key)

    async def _run_updates(tasks):
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "后台激活更新任务失败: task_index=%s exception=%r",
                    idx,
                    result,
                )

    if update_tasks:
        asyncio.create_task(_run_updates(update_tasks))
    return results

    # # 更新结果字典，保留原始搜索分数
    # updated_results = results.copy()
    # for key, update_result in zip(update_keys, update_results):
    #     if not isinstance(update_result, Exception):
    #         # 更新成功，合并原始搜索结果和更新后的激活值数据
    #         # 保留原始的 score 字段（BM25/Embedding 分数）
    #         original_nodes = results[key]
    #         updated_nodes = update_result
    #
    #         # 创建 ID 到更新节点的映射（用于快速查找激活值数据）
    #         updated_map = {node.get('id'): node for node in updated_nodes if node.get('id')}
    #
    #         # 合并数据：保留所有原始节点（包括重复的），用更新后的激活值数据填充
    #         merged_nodes = []
    #         for original_node in original_nodes:
    #             node_id = original_node.get('id')
    #             if node_id and node_id in updated_map:
    #                 # 从原始节点开始，用更新后的激活值数据覆盖
    #                 merged_node = original_node.copy()
    #
    #                 # 更新激活值相关字段
    #                 activation_fields = {
    #                     'activation_value',
    #                     'access_history',
    #                     'last_access_time',
    #                     'access_count',
    #                     'importance_score',
    #                     'version',
    #                     'statement',  # Statement 节点的内容字段
    #                     'content'  # MemorySummary 节点的内容字段
    #                 }
    #
    #                 # 只更新激活值相关字段，保留原始节点的其他字段
    #                 for field in activation_fields:
    #                     if field in updated_map[node_id]:
    #                         merged_node[field] = updated_map[node_id][field]
    #
    #                 merged_nodes.append(merged_node)
    #             else:
    #                 # 如果没有更新数据，保留原始节点
    #                 merged_nodes.append(original_node)
    #
    #         updated_results[key] = merged_nodes
    #     else:
    #         # 更新失败，记录错误但保留原始结果
    #         logger.warning(
    #             f"更新 {key} 激活值失败: {str(update_result)}"
    #         )

    # return updated_results


async def search_perceptual_by_fulltext(
        connector: Neo4jConnector,
        query: str,
        end_user_id: Optional[str] = None,
        limit: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    try:
        perceptuals = await connector.execute_query(
            SEARCH_PERCEPTUALS_BY_KEYWORD,
            query=escape_lucene_query(query),
            end_user_id=end_user_id,
            limit=limit,
        )
    except Exception as e:
        logger.warning(f"search_perceptual: keyword search failed: {e}")
        perceptuals = []

    # Deduplicate
    from app.core.memory.src.search import deduplicate_results
    perceptuals = deduplicate_results(perceptuals)

    return {"perceptuals": perceptuals}


async def search_perceptual_by_embedding(
        connector: Neo4jConnector,
        embedder_client: OpenAIEmbedderClient,
        query_text: str,
        end_user_id: Optional[str] = None,
        limit: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """Search Perceptual nodes using gds.similarity.cosine in Cypher."""
    embeddings = await embedder_client.response([query_text])
    if not embeddings or not embeddings[0]:
        logger.warning(f"search_perceptual_by_embedding: embedding generation failed for '{query_text[:50]}'")
        return {"perceptuals": []}

    try:
        perceptuals = await connector.execute_query(
            COSINE_SEARCH_CYPHER_MAPPING[Neo4jNodeType.PERCEPTUAL],
            end_user_id=end_user_id,
            embedding=embeddings[0],
            limit=limit,
        )
    except Exception as e:
        logger.warning(f"search_perceptual_by_embedding: vector search failed: {e}")
        perceptuals = []

    from app.core.memory.src.search import deduplicate_results
    perceptuals = deduplicate_results(perceptuals)

    return {"perceptuals": perceptuals}


def search_by_fulltext(
        connector: Neo4jConnector,
        node_type: Neo4jNodeType,
        end_user_id: str,
        query: str,
        limit: int = 10,
) -> Coroutine[Any, Any, list[dict[str, Any]]]:
    cypher = FULLTEXT_QUERY_CYPHER_MAPPING[node_type]
    return connector.execute_query(
        cypher,
        json_format=True,
        end_user_id=end_user_id,
        query=query,
        limit=limit,
    )


async def search_by_embedding(
        connector: Neo4jConnector,
        node_type: Neo4jNodeType,
        end_user_id: str,
        query_embedding: list[float],
        limit: int = 10,
) -> list[dict[str, Any]]:
    """Cosine similarity search using gds.similarity.cosine in Cypher, single query."""
    try:
        records = await connector.execute_query(
            COSINE_SEARCH_CYPHER_MAPPING[node_type],
            end_user_id=end_user_id,
            embedding=query_embedding,
            limit=limit,
        )
    except Exception as e:
        logger.warning(f"search_by_embedding: vector search failed: {e}, node_type:{node_type.value}",
                       exc_info=True)
        records = []

    from app.core.memory.src.search import deduplicate_results
    records = deduplicate_results(records)
    return records


async def get_nodes_by_ids(
        connector: Neo4jConnector,
        node_type: Neo4jNodeType,
        ids: list[str],
) -> list[dict[str, Any]]:
    return await connector.execute_query(
        NODE_ID_QUERY_CYPHER_MAPPING[node_type],
        ids=ids
    )


async def get_relation_between_entities(
        connector: Neo4jConnector,
        end_user_id: str,
        source_id: str,
        target_id: str,
) -> list[dict[str, Any]]:
    return await connector.execute_query(
        SEARCH_RELATION_BETWEEN_ENTITIES,
        end_user_id=end_user_id,
        source_id=source_id,
        target_id=target_id
    )


async def get_relations_between_entity_pairs(
        connector: Neo4jConnector,
        end_user_id: str,
        pairs: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Batch query relations for multiple (source_id, target_id) pairs using UNWIND."""
    if not pairs:
        return []
    return await connector.execute_query(
        SEARCH_RELATIONS_BETWEEN_ENTITY_PAIRS,
        end_user_id=end_user_id,
        pairs=pairs,
    )


async def search_graph(
        connector: Neo4jConnector,
        query: str,
        end_user_id: Optional[str] = None,
        limit: int = 50,
        include: List[Neo4jNodeType] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Search across Statements, Entities, Chunks, and Summaries using a free-text query.
    
    OPTIMIZED: Runs all queries in parallel using asyncio.gather()
    INTEGRATED: Updates activation values for knowledge nodes before returning results

    - Statements: matches s.statement CONTAINS query
    - Entities: matches e.name CONTAINS query
    - Chunks: matches s.content CONTAINS query (from Statement nodes)
    - Summaries: matches ms.content CONTAINS query

    Args:
        connector: Neo4j connector
        query: Query text for full-text search
        end_user_id: Optional group filter
        limit: Max results per category
        include: List of categories to search (default: all)

    Returns:
        Dictionary with search results per category (with updated activation values)
    """
    if include is None:
        include = [
            Neo4jNodeType.STATEMENT,
            Neo4jNodeType.CHUNK,
            Neo4jNodeType.EXTRACTEDENTITY,
            Neo4jNodeType.MEMORYSUMMARY,
            Neo4jNodeType.PERCEPTUAL
        ]

    # Escape Lucene special characters to prevent query parse errors
    escaped_query = escape_lucene_query(query)

    # Prepare tasks for parallel execution
    tasks = []
    task_keys = []

    for node_type in include:
        tasks.append(search_by_fulltext(connector, node_type, end_user_id, escaped_query, limit))
        task_keys.append(node_type.value)

    # Execute all queries in parallel
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Build results dictionary
    results = {}
    for key, result in zip(task_keys, task_results):
        if isinstance(result, Exception):
            logger.warning(f"search_graph: {key} 关键词查询异常: {result}")
            results[key] = []
        else:
            results[key] = result

    # Deduplicate results before updating activation values
    # This prevents duplicates from propagating through the pipeline
    from app.core.memory.src.search import deduplicate_results
    for key in results:
        if isinstance(results[key], list):
            results[key] = deduplicate_results(results[key])

    # 更新知识节点的激活值（Statement, ExtractedEntity, MemorySummary）
    # Skip activation updates if only searching summaries (optimization)
    needs_activation_update = any(
        key in include and key in results and results[key]
        for key in [Neo4jNodeType.STATEMENT, Neo4jNodeType.EXTRACTEDENTITY, Neo4jNodeType.MEMORYSUMMARY]
    )

    if needs_activation_update:
        results = await _update_search_results_activation(
            connector=connector,
            results=results,
            end_user_id=end_user_id
        )

    return results


async def search_graph_by_embedding(
        connector: Neo4jConnector,
        embedder_client: RedBearEmbeddings | OpenAIEmbedderClient,
        query_text: str,
        end_user_id: str,
        limit: int = 50,
        include=None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Embedding-based semantic search across Statements, Chunks, and Entities.
    
    OPTIMIZED: Runs all queries in parallel using asyncio.gather()
    INTEGRATED: Updates activation values for knowledge nodes before returning results

    - Computes query embedding with the provided embedder_client
    - Ranks by cosine similarity in Cypher
    - Filters by end_user_id if provided
    - Returns up to 'limit' per included type
    """
    if include is None:
        include = [
            Neo4jNodeType.STATEMENT,
            Neo4jNodeType.CHUNK,
            Neo4jNodeType.EXTRACTEDENTITY,
            Neo4jNodeType.MEMORYSUMMARY,
            Neo4jNodeType.PERCEPTUAL
        ]

    if isinstance(embedder_client, RedBearEmbeddings):
        embeddings = embedder_client.embed_documents([query_text])
    else:
        embeddings = await embedder_client.response([query_text])
    if not embeddings or not embeddings[0]:
        logger.warning(f"search_graph_by_embedding: embedding generation failed for '{query_text[:50]}'")
        return {search_key: [] for search_key in include}
    embedding = embeddings[0]

    # Prepare tasks for parallel execution
    tasks = []
    task_keys = []

    for node_type in include:
        tasks.append(search_by_embedding(connector, node_type, end_user_id, embedding, limit * 2))
        task_keys.append(node_type.value)

    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Build results dictionary
    results: Dict[str, List[Dict[str, Any]]] = {}

    for key, result in zip(task_keys, task_results):
        if isinstance(result, Exception):
            logger.warning(f"search_graph_by_embedding: {key} 向量查询异常: {result}")
            results[key] = []
        else:
            results[key] = result

    # Deduplicate results before updating activation values
    # This prevents duplicates from propagating through the pipeline
    from app.core.memory.src.search import deduplicate_results
    for key in results:
        if isinstance(results[key], list):
            results[key] = deduplicate_results(results[key])

    # 更新知识节点的激活值（Statement, ExtractedEntity, MemorySummary）
    # Skip activation updates if only searching summaries (optimization)
    needs_activation_update = any(
        key in include and key in results and results[key]
        for key in [Neo4jNodeType.STATEMENT, Neo4jNodeType.EXTRACTEDENTITY, Neo4jNodeType.MEMORYSUMMARY]
    )

    if needs_activation_update:
        update_start = time.time()
        results = await _update_search_results_activation(
            connector=connector,
            results=results,
            end_user_id=end_user_id
        )
        update_time = time.time() - update_start
        logger.info(f"[PERF] Activation value updates took: {update_time:.4f}s")
    else:
        logger.info("[PERF] Skipping activation updates (only summaries)")

    return results


async def search_graph_by_relationship(
        connector: Neo4jConnector,
        end_user_id: str,
        source_id: str,
        predicates: list[int],
):
    related_node = await connector.execute_query(
        SEARCH_ENITITES_BY_RELATIONSHIP,
        end_user_id=end_user_id,
        source_id=source_id,
        predicates=predicates,
    )
    return related_node


async def search_user_metadata(
        connector: Neo4jConnector,
        end_user_id: str
) -> dict:
    user_info = await connector.execute_query(
        SEARCH_USER_METADATA,
        end_user_id=end_user_id
    )
    return user_info[0] if user_info else {}


async def get_dedup_candidates_for_entities(  # 适配新版查询：使用全文索引按名称检索候选实体
        connector: Neo4jConnector,
        end_user_id: str,
        entities: List[Dict[str, Any]],
        use_contains_fallback: bool = True,
        batch_size: int = 500,
        max_concurrency: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    为第二层去重消歧批量检索候选实体（适配新版 cypher_queries）：
    - 使用全文索引查询 `SEARCH_ENTITIES_BY_NAME` 按 (end_user_id, name) 检索候选；
    - 保留并发控制与返回结构（incoming_id -> [db_entity_props...]）；
    - 若提供 `entity_type`，在本地对返回结果做类型过滤；
    - `use_contains_fallback` 保留形参以兼容，必要时可扩展二次查询策略。

    返回：incoming_id -> [db_entity_props...]
    """

    if not entities:
        return {}

    sem = asyncio.Semaphore(max_concurrency)

    async def _query_by_name(incoming: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
        async with sem:
            inc_id = incoming.get("id") or "__unknown__"
            name = (incoming.get("name") or "").strip()
            if not name:
                return inc_id, []
            try:
                # 全文索引按名称检索（包含 CONTAINS 语义）
                rows = await connector.execute_query(
                    SEARCH_ENTITIES_BY_NAME,
                    query=escape_lucene_query(name),
                    end_user_id=end_user_id,
                    limit=100,
                )
            except Exception:
                rows = []

            # 可选本地类型过滤（若输入实体提供类型）
            typ = incoming.get("entity_type")
            if typ:
                try:
                    rows = [r for r in rows if (r.get("entity_type") == typ)]
                except Exception:
                    pass

            # 注入 incoming_id 以保持兼容下游合并逻辑
            for r in rows:
                r["incoming_id"] = inc_id

            # 简单的降级：若为空且允许 fallback，可按小写名再次查询
            if use_contains_fallback and not rows and name:
                try:
                    rows = await connector.execute_query(
                        SEARCH_ENTITIES_BY_NAME,
                        query=escape_lucene_query(name.lower()),
                        end_user_id=end_user_id,
                        limit=100,
                    )
                    for r in rows:
                        r["incoming_id"] = inc_id
                except Exception:
                    pass

            return inc_id, rows

    tasks = [_query_by_name(e) for e in entities]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: Dict[str, List[Dict[str, Any]]] = {}
    for res in results:
        if isinstance(res, Exception):
            # 静默跳过单条失败
            continue
        inc_id, rows = res
        inc_id = inc_id or "__unknown__"
        merged.setdefault(inc_id, [])
        existing_ids = {x.get("id") for x in merged[inc_id]}
        for rec in rows:
            if rec.get("id") not in existing_ids:
                merged[inc_id].append(rec)
    return merged


async def search_graph_by_keyword_temporal(
        connector: Neo4jConnector,
        query_text: str,
        end_user_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        valid_date: Optional[str] = None,
        invalid_date: Optional[str] = None,
        limit: int = 50,
) -> Dict[str, List[Any]]:
    """
    Temporal keyword search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements containing query_text created between start_date and end_date
    - Optionally filters by end_user_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    if not query_text:
        logger.warning("query_text不能为空")
        return {"statements": []}
    escaped_query = escape_lucene_query(query_text)
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL,
        query=escaped_query,
        end_user_id=end_user_id,
        start_date=start_date,
        end_date=end_date,
        valid_date=valid_date,
        invalid_date=invalid_date,
        limit=limit,
    )
    logger.debug(f"查询结果为：\n{statements}")

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results


async def search_graph_by_temporal(
        connector: Neo4jConnector,
        end_user_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        valid_date: Optional[str] = None,
        invalid_date: Optional[str] = None,
        limit: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements created between start_date and end_date
    - Optionally filters by end_user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_TEMPORAL,
        end_user_id=end_user_id,
        start_date=start_date,
        end_date=end_date,
        valid_date=valid_date,
        invalid_date=invalid_date,
        limit=limit,
    )

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results


async def search_graph_by_dialog_id(
        connector: Neo4jConnector,
        dialog_id: str,
        end_user_id: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Dialogues.

    - Matches dialogues with dialog_id
    - Optionally filters by end_user_id
    - Returns up to 'limit' dialogues
    """
    if not dialog_id:
        logger.warning("dialog_id不能为空")
        return {"dialogues": []}

    dialogues = await connector.execute_query(
        SEARCH_DIALOGUE_BY_DIALOG_ID,
        end_user_id=end_user_id,
        dialog_id=dialog_id,
        limit=limit,
    )
    return {"dialogues": dialogues}


async def search_graph_by_chunk_id(
        connector: Neo4jConnector,
        chunk_id: str,
        end_user_id: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    if not chunk_id:
        logger.warning("chunk_id不能为空")
        return {"chunks": []}
    chunks = await connector.execute_query(
        SEARCH_CHUNK_BY_CHUNK_ID,
        end_user_id=end_user_id,
        chunk_id=chunk_id,
        limit=limit,
    )
    return {"chunks": chunks}


async def search_graph_community_expand(
        connector: Neo4jConnector,
        community_ids: List[str],
        end_user_id: str,
        limit: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    三期：社区展开检索 —— 主题 → 细节两级检索。

    命中 Community 节点后，沿 BELONGS_TO_COMMUNITY 关系拉取成员实体，
    再沿 REFERENCES_ENTITY 关系拉取关联的 Statement 节点，
    按 activation_value 降序返回，实现"主题摘要 → 具体记忆"的深度召回。

    Args:
        connector: Neo4j 连接器
        community_ids: 已命中的社区 ID 列表
        end_user_id: 用户 ID，用于数据隔离
        limit: 每个社区最多返回的 Statement 数量

    Returns:
        {"expanded_statements": [Statement 列表，含 community_name / source_entity 字段]}
    """
    if not community_ids or not end_user_id:
        return {"expanded_statements": []}

    tasks = [
        connector.execute_query(
            EXPAND_COMMUNITY_STATEMENTS,
            community_id=cid,
            end_user_id=end_user_id,
            limit=limit,
        )
        for cid in community_ids
    ]

    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    expanded: List[Dict[str, Any]] = []
    for cid, result in zip(community_ids, task_results):
        if isinstance(result, Exception):
            logger.warning(f"社区展开检索失败 community_id={cid}: {result}")
        else:
            expanded.extend(result)

    # 按 activation_value 全局排序后去重
    from app.core.memory.src.search import deduplicate_results
    expanded.sort(
        key=lambda x: float(x.get("activation_value") or 0),
        reverse=True,
    )
    expanded = deduplicate_results(expanded)

    logger.info(f"社区展开检索完成: community_ids={community_ids}, 展开 statements={len(expanded)}")
    return {"expanded_statements": expanded}


async def search_graph_by_created_at(
        connector: Neo4jConnector,
        end_user_id: Optional[str] = None,

        created_at: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements created at created_at
    - Optionally filters by end_user_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_CREATED_AT,
        end_user_id=end_user_id,

        created_at=created_at,
        limit=limit,
    )

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results


async def search_graph_by_valid_at(
        connector: Neo4jConnector,
        end_user_id: Optional[str] = None,

        valid_at: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements valid at valid_at
    - Optionally filters by end_user_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_VALID_AT,
        end_user_id=end_user_id,

        valid_at=valid_at,
        limit=limit,
    )

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results


async def search_graph_g_created_at(
        connector: Neo4jConnector,
        end_user_id: Optional[str] = None,

        created_at: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements created at created_at
    - Optionally filters by end_user_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_G_CREATED_AT,
        end_user_id=end_user_id,

        created_at=created_at,
        limit=limit,
    )

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results


async def search_graph_g_valid_at(
        connector: Neo4jConnector,
        end_user_id: Optional[str] = None,

        valid_at: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements valid at valid_at
    - Optionally filters by end_user_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_G_VALID_AT,
        end_user_id=end_user_id,
        valid_at=valid_at,
        limit=limit,
    )

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results


async def search_graph_l_created_at(
        connector: Neo4jConnector,
        end_user_id: Optional[str] = None,

        created_at: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements created at created_at
    - Optionally filters by end_user_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_L_CREATED_AT,
        end_user_id=end_user_id,

        created_at=created_at,
        limit=limit,
    )

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results


async def search_graph_l_valid_at(
        connector: Neo4jConnector,
        end_user_id: Optional[str] = None,

        valid_at: Optional[str] = None,
        limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.
    
    INTEGRATED: Updates activation values for Statement nodes before returning results

    - Matches statements valid at valid_at
    - Optionally filters by end_user_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_L_VALID_AT,
        end_user_id=end_user_id,

        valid_at=valid_at,
        limit=limit,
    )

    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )

    return results
