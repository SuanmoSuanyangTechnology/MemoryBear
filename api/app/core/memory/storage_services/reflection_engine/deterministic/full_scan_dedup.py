"""子问题 3 · 方案B：低频全量扫描去重"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.aioRedis import get_redis_connection
from app.core.utils.datetime_utils import to_iso_z, utcnow
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


def _scan_time_key(end_user_id: str, entity_type: str) -> str:
    return f"dedup:full_scan:{end_user_id}:{entity_type}"


async def get_last_scan_time(end_user_id: str, entity_type: str) -> Optional[str]:
    """获取上次扫描时间（ISO格式），None 表示从未扫描"""
    redis = await get_redis_connection()
    val = await redis.get(_scan_time_key(end_user_id, entity_type))
    if val is None:
        return None
    return val if isinstance(val, str) else val.decode()


async def update_scan_time(end_user_id: str, entity_type: str) -> None:
    """更新扫描时间为当前时间（永久保留，不设TTL）"""
    redis = await get_redis_connection()
    now = to_iso_z(utcnow())
    await redis.set(_scan_time_key(end_user_id, entity_type), now)


async def get_entity_types(
    connector: Neo4jConnector,
    end_user_id: str,
) -> List[Dict]:
    """查询用户所有 entity_type 及数量"""
    from app.repositories.neo4j.cypher_queries import DEDUP_FULL_SCAN_ENTITY_TYPES
    return await connector.execute_query(
        DEDUP_FULL_SCAN_ENTITY_TYPES,
        end_user_id=end_user_id,
    )


async def check_new_entities(
    connector: Neo4jConnector,
    end_user_id: str,
    entity_type: str,
    last_scan_time: str,
) -> int:
    """查询上次扫描后新增的实体数"""
    from app.repositories.neo4j.cypher_queries import DEDUP_FULL_SCAN_NEW_COUNT
    rows = await connector.execute_query(
        DEDUP_FULL_SCAN_NEW_COUNT,
        end_user_id=end_user_id,
        entity_type=entity_type,
        last_scan_time=last_scan_time,
    )
    return rows[0]["new_count"] if rows else 0


async def fetch_entities_by_type(
    connector: Neo4jConnector,
    end_user_id: str,
    entity_type: str,
) -> List[Dict]:
    """查出该类型下所有实体（全量送 LLM）"""
    from app.repositories.neo4j.cypher_queries import DEDUP_FULL_SCAN_ENTITIES
    return await connector.execute_query(
        DEDUP_FULL_SCAN_ENTITIES,
        end_user_id=end_user_id,
        entity_type=entity_type,
    )
