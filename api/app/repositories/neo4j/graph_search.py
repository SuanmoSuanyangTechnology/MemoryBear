import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.repositories.neo4j.cypher_queries import (
    CHUNK_EMBEDDING_SEARCH,
    ENTITY_EMBEDDING_SEARCH,
    MEMORY_SUMMARY_EMBEDDING_SEARCH,
    SEARCH_CHUNK_BY_CHUNK_ID,
    SEARCH_CHUNKS_BY_CONTENT,
    SEARCH_DIALOGUE_BY_DIALOG_ID,
    SEARCH_ENTITIES_BY_NAME,
    SEARCH_MEMORY_SUMMARIES_BY_KEYWORD,
    SEARCH_STATEMENTS_BY_CREATED_AT,
    SEARCH_STATEMENTS_BY_KEYWORD,
    SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL,
    SEARCH_STATEMENTS_BY_TEMPORAL,
    SEARCH_STATEMENTS_BY_VALID_AT,
    SEARCH_STATEMENTS_G_CREATED_AT,
    SEARCH_STATEMENTS_G_VALID_AT,
    SEARCH_STATEMENTS_L_CREATED_AT,
    SEARCH_STATEMENTS_L_VALID_AT,
    STATEMENT_EMBEDDING_SEARCH,
)

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


async def _update_activation_values_batch(
    connector: Neo4jConnector,
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
        logger.warning(f"批量更新激活值：没有有效的节点ID")
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
        
        logger.info(
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
        'summaries': 'MemorySummary'
    }
    
    # 并行更新所有类型的节点
    update_tasks = []
    update_keys = []
    
    for key, label in knowledge_node_types.items():
        if key in results and results[key]:
            update_tasks.append(
                _update_activation_values_batch(
                    connector=connector,
                    nodes=results[key],
                    node_label=label,
                    end_user_id=end_user_id
                )
            )
            update_keys.append(key)
    
    if not update_tasks:
        return results
    
    # 并行执行所有更新
    update_results = await asyncio.gather(*update_tasks, return_exceptions=True)
    
    # 更新结果字典，保留原始搜索分数
    updated_results = results.copy()
    for key, update_result in zip(update_keys, update_results):
        if not isinstance(update_result, Exception):
            # 更新成功，合并原始搜索结果和更新后的激活值数据
            # 保留原始的 score 字段（BM25/Embedding 分数）
            original_nodes = results[key]
            updated_nodes = update_result
            
            # 创建 ID 到更新节点的映射（用于快速查找激活值数据）
            updated_map = {node.get('id'): node for node in updated_nodes if node.get('id')}
            
            # 合并数据：保留所有原始节点（包括重复的），用更新后的激活值数据填充
            merged_nodes = []
            for original_node in original_nodes:
                node_id = original_node.get('id')
                if node_id and node_id in updated_map:
                    # 从原始节点开始，用更新后的激活值数据覆盖
                    merged_node = original_node.copy()
                    
                    # 更新激活值相关字段
                    activation_fields = {
                        'activation_value',
                        'access_history',
                        'last_access_time',
                        'access_count',
                        'importance_score',
                        'version',
                        'statement',  # Statement 节点的内容字段
                        'content'     # MemorySummary 节点的内容字段
                    }
                    
                    # 只更新激活值相关字段，保留原始节点的其他字段
                    for field in activation_fields:
                        if field in updated_map[node_id]:
                            merged_node[field] = updated_map[node_id][field]
                    
                    merged_nodes.append(merged_node)
                else:
                    # 如果没有更新数据，保留原始节点
                    merged_nodes.append(original_node)
            
            updated_results[key] = merged_nodes
        else:
            # 更新失败，记录错误但保留原始结果
            logger.warning(
                f"更新 {key} 激活值失败: {str(update_result)}"
            )
    
    return updated_results


async def search_graph(
    connector: Neo4jConnector,
    q: str,
    end_user_id: Optional[str] = None,
    limit: int = 50,
    include: List[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Search across Statements, Entities, Chunks, and Summaries using a free-text query.
    
    OPTIMIZED: Runs all queries in parallel using asyncio.gather()
    INTEGRATED: Updates activation values for knowledge nodes before returning results

    - Statements: matches s.statement CONTAINS q
    - Entities: matches e.name CONTAINS q
    - Chunks: matches s.content CONTAINS q (from Statement nodes)
    - Summaries: matches ms.content CONTAINS q

    Args:
        connector: Neo4j connector
        q: Query text
        end_user_id: Optional group filter
        limit: Max results per category
        include: List of categories to search (default: all)

    Returns:
        Dictionary with search results per category (with updated activation values)
    """
    if include is None:
        include = ["statements", "chunks", "entities", "summaries"]
    
    # Prepare tasks for parallel execution
    tasks = []
    task_keys = []
    
    if "statements" in include:
        tasks.append(connector.execute_query(
            SEARCH_STATEMENTS_BY_KEYWORD,
            q=q,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("statements")
    
    if "entities" in include:
        tasks.append(connector.execute_query(
            SEARCH_ENTITIES_BY_NAME,
            q=q,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("entities")
    
    if "chunks" in include:
        tasks.append(connector.execute_query(
            SEARCH_CHUNKS_BY_CONTENT,
            q=q,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("chunks")
    
    if "summaries" in include:
        tasks.append(connector.execute_query(
            SEARCH_MEMORY_SUMMARIES_BY_KEYWORD,
            q=q,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("summaries")
    
    # Execute all queries in parallel
    task_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Build results dictionary
    results = {}
    for key, result in zip(task_keys, task_results):
        if isinstance(result, Exception):
            results[key] = []
        else:
            results[key] = result
    
    # Deduplicate results before updating activation values
    # This prevents duplicates from propagating through the pipeline
    from app.core.memory.src.search import _deduplicate_results
    for key in results:
        if isinstance(results[key], list):
            results[key] = _deduplicate_results(results[key])
    
    # 更新知识节点的激活值（Statement, ExtractedEntity, MemorySummary）
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )
    
    return results


async def search_graph_by_embedding(
    connector: Neo4jConnector,
    embedder_client,
    query_text: str,
    end_user_id: Optional[str] = None,
    limit: int = 50,
    include: List[str] = ["statements", "chunks", "entities","summaries"],
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
    import time
    
    # Get embedding for the query
    embed_start = time.time()
    embeddings = await embedder_client.response([query_text])
    embed_time = time.time() - embed_start
    print(f"[PERF] Embedding generation took: {embed_time:.4f}s")
    
    if not embeddings or not embeddings[0]:
        return {"statements": [], "chunks": [], "entities": [], "summaries": []}
    embedding = embeddings[0]

    # Prepare tasks for parallel execution
    tasks = []
    task_keys = []

    # Statements (embedding)
    if "statements" in include:
        tasks.append(connector.execute_query(
            STATEMENT_EMBEDDING_SEARCH,
            embedding=embedding,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("statements")

    # Chunks (embedding)
    if "chunks" in include:
        tasks.append(connector.execute_query(
            CHUNK_EMBEDDING_SEARCH,
            embedding=embedding,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("chunks")

    # Entities
    if "entities" in include:
        tasks.append(connector.execute_query(
            ENTITY_EMBEDDING_SEARCH,
            embedding=embedding,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("entities")

    # Memory summaries
    if "summaries" in include:
        tasks.append(connector.execute_query(
            MEMORY_SUMMARY_EMBEDDING_SEARCH,
            embedding=embedding,
            end_user_id=end_user_id,
            limit=limit,
        ))
        task_keys.append("summaries")

    # Execute all queries in parallel
    query_start = time.time()
    task_results = await asyncio.gather(*tasks, return_exceptions=True)
    query_time = time.time() - query_start
    print(f"[PERF] Neo4j queries (parallel) took: {query_time:.4f}s")
    
    # Build results dictionary
    results: Dict[str, List[Dict[str, Any]]] = {
        "statements": [],
        "chunks": [],
        "entities": [],
        "summaries": [],
    }
    
    for key, result in zip(task_keys, task_results):
        if isinstance(result, Exception):
            results[key] = []
        else:
            results[key] = result

    # Deduplicate results before updating activation values
    # This prevents duplicates from propagating through the pipeline
    from app.core.memory.src.search import _deduplicate_results
    for key in results:
        if isinstance(results[key], list):
            results[key] = _deduplicate_results(results[key])

    # 更新知识节点的激活值（Statement, ExtractedEntity, MemorySummary）
    update_start = time.time()
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )
    update_time = time.time() - update_start
    print(f"[PERF] Activation value updates took: {update_time:.4f}s")

    return results
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
                    q=name,
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
                        q=name.lower(),
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
        print(f"query_text不能为空")
        return {"statements": []}
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL,
        q=query_text,
        end_user_id=end_user_id,
        start_date=start_date,
        end_date=end_date,
        valid_date=valid_date,
        invalid_date=invalid_date,
        limit=limit,
    )
    print(f"查询结果为：\n{statements}")

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

    print(f"查询语句为：\n{SEARCH_STATEMENTS_BY_TEMPORAL}")
    print(f"查询参数为：\n{{end_user_id: {end_user_id}, start_date: {start_date}, end_date: {end_date}, valid_date: {valid_date}, invalid_date: {invalid_date}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    
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
        print(f"dialog_id不能为空")
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
    chunk_id : str,
    end_user_id: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    if not chunk_id:
        print(f"chunk_id不能为空")
        return {"chunks": []}
    chunks = await connector.execute_query(
        SEARCH_CHUNK_BY_CHUNK_ID,
        end_user_id=end_user_id,
        chunk_id=chunk_id,
        limit=limit,
    )
    return {"chunks": chunks}


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

    print(f"查询语句为：\n{SEARCH_STATEMENTS_BY_CREATED_AT}")
    print(f"查询参数为：\n{{end_user_id: {end_user_id} created_at: {created_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    
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

    print(f"查询语句为：\n{SEARCH_STATEMENTS_BY_VALID_AT}")
    print(f"查询参数为：\n{{end_user_id: {end_user_id}， valid_at: {valid_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    
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

    print(f"查询语句为：\n{SEARCH_STATEMENTS_G_CREATED_AT}")
    print(f"查询参数为：\n{{end_user_id: {end_user_id}, created_at: {created_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    
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

    print(f"查询语句为：\n{SEARCH_STATEMENTS_G_VALID_AT}")
    print(f"查询参数为：\n{{end_user_id: {end_user_id}, valid_at: {valid_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    
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

    print(f"查询语句为：\n{SEARCH_STATEMENTS_L_CREATED_AT}")
    print(f"查询参数为：\n{{end_user_id: {end_user_id}, created_at: {created_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    
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

    print(f"查询语句为：\n{SEARCH_STATEMENTS_L_VALID_AT}")
    print(f"查询参数为：\n{{end_user_id: {end_user_id},  valid_at: {valid_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    
    # 更新 Statement 节点的激活值
    results = {"statements": statements}
    results = await _update_search_results_activation(
        connector=connector,
        results=results,
        end_user_id=end_user_id
    )
    
    return results
