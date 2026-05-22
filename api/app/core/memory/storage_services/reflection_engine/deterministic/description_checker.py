"""子问题 6 · 确定性层：描述合并候选筛选"""
import logging
from typing import Dict, List
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import REFLECTION_DESC_MERGE_CANDIDATES

logger = logging.getLogger(__name__)


def should_merge_description(description: str, min_fragments: int = 5) -> bool:
    """判断单个实体是否需要合并（工具函数，供其他模块调用）"""
    if not description:
        return False
    fragments = [f.strip() for f in description.split('；') if f.strip()]
    return len(fragments) >= min_fragments


async def scan_merge_candidates(
    connector: Neo4jConnector,
    end_user_id: str,
    min_fragments: int = 5,
    batch_size: int = 30,
) -> List[Dict]:
    """Cypher 批量查询候选实体"""
    results = await connector.execute_query(
        REFLECTION_DESC_MERGE_CANDIDATES,
        end_user_id=end_user_id,
        min_fragments=min_fragments,
        batch_size=batch_size,
    )
    return results