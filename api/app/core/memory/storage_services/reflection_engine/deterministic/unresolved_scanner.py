"""子问题 5 · 确定性层：未识别实体候选筛选"""
import logging
from typing import Dict, List
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import (
    UNRESOLVED_STATEMENT_CANDIDATES,
    UNRESOLVED_CONTEXT_CHUNKS,
)

logger = logging.getLogger(__name__)


async def scan_unresolved_candidates(
    connector: Neo4jConnector,
    end_user_id: str,
    batch_size: int = 30,
) -> List[Dict]:
    """查询所有 has_unsolved_reference=true 的 Statement"""
    results = await connector.execute_query(
        UNRESOLVED_STATEMENT_CANDIDATES,
        end_user_id=end_user_id,
        batch_size=batch_size,
    )
    return results


async def fetch_context_chunks(
    connector: Neo4jConnector,
    chunk_id: str,
    end_user_id: str,
    limit: int = 10,
) -> List[str]:
    """通过 chunk_id 取时间最近的 N 条 Chunk 原文作为上下文"""
    if not chunk_id:
        return []
    results = await connector.execute_query(
        UNRESOLVED_CONTEXT_CHUNKS,
        chunk_id=chunk_id,
        end_user_id=end_user_id,
        limit=limit,
    )
    return [r["content"] for r in results]
