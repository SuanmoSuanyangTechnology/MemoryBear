from typing import Any, Dict, List, Optional
import asyncio

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import (
    SEARCH_STATEMENTS_BY_KEYWORD,
    SEARCH_ENTITIES_BY_NAME,
    SEARCH_CHUNKS_BY_CONTENT,
    STATEMENT_EMBEDDING_SEARCH,
    CHUNK_EMBEDDING_SEARCH,
    ENTITY_EMBEDDING_SEARCH,
    SEARCH_MEMORY_SUMMARIES_BY_KEYWORD,
    MEMORY_SUMMARY_EMBEDDING_SEARCH,
    SEARCH_STATEMENTS_BY_TEMPORAL,
    SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL,
    SEARCH_DIALOGUE_BY_DIALOG_ID,
    SEARCH_CHUNK_BY_CHUNK_ID,
    SEARCH_STATEMENTS_BY_CREATED_AT,
    SEARCH_STATEMENTS_BY_VALID_AT,
    SEARCH_STATEMENTS_G_CREATED_AT,
    SEARCH_STATEMENTS_L_CREATED_AT,
    SEARCH_STATEMENTS_G_VALID_AT,
    SEARCH_STATEMENTS_L_VALID_AT,
)


async def search_graph(
    connector: Neo4jConnector,
    q: str,
    group_id: Optional[str] = None,
    limit: int = 50,
    include: List[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Search across Statements, Entities, Chunks, and Summaries using a free-text query.
    
    OPTIMIZED: Runs all queries in parallel using asyncio.gather()

    - Statements: matches s.statement CONTAINS q
    - Entities: matches e.name CONTAINS q
    - Chunks: matches s.content CONTAINS q (from Statement nodes)
    - Summaries: matches ms.content CONTAINS q

    Args:
        connector: Neo4j connector
        q: Query text
        group_id: Optional group filter
        limit: Max results per category
        include: List of categories to search (default: all)

    Returns:
        Dictionary with search results per category
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
            group_id=group_id,
            limit=limit,
        ))
        task_keys.append("statements")
    
    if "entities" in include:
        tasks.append(connector.execute_query(
            SEARCH_ENTITIES_BY_NAME,
            q=q,
            group_id=group_id,
            limit=limit,
        ))
        task_keys.append("entities")
    
    if "chunks" in include:
        tasks.append(connector.execute_query(
            SEARCH_CHUNKS_BY_CONTENT,
            q=q,
            group_id=group_id,
            limit=limit,
        ))
        task_keys.append("chunks")
    
    if "summaries" in include:
        tasks.append(connector.execute_query(
            SEARCH_MEMORY_SUMMARIES_BY_KEYWORD,
            q=q,
            group_id=group_id,
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
    
    return results


async def search_graph_by_embedding(
    connector: Neo4jConnector,
    embedder_client,
    query_text: str,
    group_id: Optional[str] = None,
    limit: int = 50,
    include: List[str] = ["statements", "chunks", "entities","summaries"],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Embedding-based semantic search across Statements, Chunks, and Entities.
    
    OPTIMIZED: Runs all queries in parallel using asyncio.gather()

    - Computes query embedding with the provided embedder_client
    - Ranks by cosine similarity in Cypher
    - Filters by group_id if provided
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
            group_id=group_id,
            limit=limit,
        ))
        task_keys.append("statements")

    # Chunks (embedding)
    if "chunks" in include:
        tasks.append(connector.execute_query(
            CHUNK_EMBEDDING_SEARCH,
            embedding=embedding,
            group_id=group_id,
            limit=limit,
        ))
        task_keys.append("chunks")

    # Entities
    if "entities" in include:
        tasks.append(connector.execute_query(
            ENTITY_EMBEDDING_SEARCH,
            embedding=embedding,
            group_id=group_id,
            limit=limit,
        ))
        task_keys.append("entities")

    # Memory summaries
    if "summaries" in include:
        tasks.append(connector.execute_query(
            MEMORY_SUMMARY_EMBEDDING_SEARCH,
            embedding=embedding,
            group_id=group_id,
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

    return results
async def get_dedup_candidates_for_entities(  # 适配新版查询：使用全文索引按名称检索候选实体
    connector: Neo4jConnector,
    group_id: str,
    entities: List[Dict[str, Any]],
    use_contains_fallback: bool = True,
    batch_size: int = 500,
    max_concurrency: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    为第二层去重消歧批量检索候选实体（适配新版 cypher_queries）：
    - 使用全文索引查询 `SEARCH_ENTITIES_BY_NAME` 按 (group_id, name) 检索候选；
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
                    group_id=group_id,
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
                        group_id=group_id,
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
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    valid_date: Optional[str] = None,
    invalid_date: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, List[Any]]:
    """
    Temporal keyword search across Statements.

    - Matches statements containing query_text created between start_date and end_date
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    if not query_text:
        print(f"query_text不能为空")
        return {"statements": []}
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL,
        q=query_text,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        valid_date=valid_date,
        invalid_date=invalid_date,
        limit=limit,
    )
    print(f"查询结果为：\n{statements}")

    return {"statements": statements}


async def search_graph_by_temporal(
    connector: Neo4jConnector,
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    valid_date: Optional[str] = None,
    invalid_date: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.

    - Matches statements created between start_date and end_date
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_TEMPORAL,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        valid_date=valid_date,
        invalid_date=invalid_date,
        limit=limit,
    )

    print(f"查询语句为：\n{SEARCH_STATEMENTS_BY_TEMPORAL}")
    print(f"查询参数为：\n{{group_id: {group_id}, apply_id: {apply_id}, user_id: {user_id}, start_date: {start_date}, end_date: {end_date}, valid_date: {valid_date}, invalid_date: {invalid_date}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    return {"statements": statements}


async def search_graph_by_dialog_id(
    connector: Neo4jConnector,
    dialog_id: str,
    group_id: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Dialogues.

    - Matches dialogues with dialog_id
    - Optionally filters by group_id
    - Returns up to 'limit' dialogues
    """
    if not dialog_id:
        print(f"dialog_id不能为空")
        return {"dialogues": []}

    dialogues = await connector.execute_query(
        SEARCH_DIALOGUE_BY_DIALOG_ID,
        group_id=group_id,
        dialog_id=dialog_id,
        limit=limit,
    )
    return {"dialogues": dialogues}


async def search_graph_by_chunk_id(
    connector: Neo4jConnector,
    chunk_id : str,
    group_id: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    if not chunk_id:
        print(f"chunk_id不能为空")
        return {"chunks": []}
    chunks = await connector.execute_query(
        SEARCH_CHUNK_BY_CHUNK_ID,
        group_id=group_id,
        chunk_id=chunk_id,
        limit=limit,
    )
    return {"chunks": chunks}


async def search_graph_by_created_at(
    connector: Neo4jConnector,
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    created_at: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.

    - Matches statements created at created_at
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_CREATED_AT,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        created_at=created_at,
        limit=limit,
    )

    print(f"查询语句为：\n{SEARCH_STATEMENTS_BY_CREATED_AT}")
    print(f"查询参数为：\n{{group_id: {group_id}, apply_id: {apply_id}, user_id: {user_id}, created_at: {created_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    return {"statements": statements}

async def search_graph_by_valid_at(
    connector: Neo4jConnector,
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    valid_at: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.

    - Matches statements valid at valid_at
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_BY_VALID_AT,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        valid_at=valid_at,
        limit=limit,
    )

    print(f"查询语句为：\n{SEARCH_STATEMENTS_BY_VALID_AT}")
    print(f"查询参数为：\n{{group_id: {group_id}, apply_id: {apply_id}, user_id: {user_id}, valid_at: {valid_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    return {"statements": statements}

async def search_graph_g_created_at(
    connector: Neo4jConnector,
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    created_at: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.

    - Matches statements created at created_at
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_G_CREATED_AT,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        created_at=created_at,
        limit=limit,
    )

    print(f"查询语句为：\n{SEARCH_STATEMENTS_G_CREATED_AT}")
    print(f"查询参数为：\n{{group_id: {group_id}, apply_id: {apply_id}, user_id: {user_id}, created_at: {created_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    return {"statements": statements}

async def search_graph_g_valid_at(
    connector: Neo4jConnector,
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    valid_at: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.

    - Matches statements valid at valid_at
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_G_VALID_AT,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        valid_at=valid_at,
        limit=limit,
    )

    print(f"查询语句为：\n{SEARCH_STATEMENTS_G_VALID_AT}")
    print(f"查询参数为：\n{{group_id: {group_id}, apply_id: {apply_id}, user_id: {user_id}, valid_at: {valid_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    return {"statements": statements}

async def search_graph_l_created_at(
    connector: Neo4jConnector,
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    created_at: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.

    - Matches statements created at created_at
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_L_CREATED_AT,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        created_at=created_at,
        limit=limit,
    )

    print(f"查询语句为：\n{SEARCH_STATEMENTS_L_CREATED_AT}")
    print(f"查询参数为：\n{{group_id: {group_id}, apply_id: {apply_id}, user_id: {user_id}, created_at: {created_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    return {"statements": statements}

async def search_graph_l_valid_at(
    connector: Neo4jConnector,
    group_id: Optional[str] = None,
    apply_id: Optional[str] = None,
    user_id: Optional[str] = None,
    valid_at: Optional[str] = None,
    limit: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Temporal search across Statements.

    - Matches statements valid at valid_at
    - Optionally filters by group_id, apply_id, user_id
    - Returns up to 'limit' statements
    """
    statements = await connector.execute_query(
        SEARCH_STATEMENTS_L_VALID_AT,
        group_id=group_id,
        apply_id=apply_id,
        user_id=user_id,
        valid_at=valid_at,
        limit=limit,
    )

    print(f"查询语句为：\n{SEARCH_STATEMENTS_L_VALID_AT}")
    print(f"查询参数为：\n{{group_id: {group_id}, apply_id: {apply_id}, user_id: {user_id}, valid_at: {valid_at}, limit: {limit}}}")
    print(f"查询结果为：\n{statements}")
    return {"statements": statements}
