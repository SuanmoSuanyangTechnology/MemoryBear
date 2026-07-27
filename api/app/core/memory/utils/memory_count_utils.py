import asyncio
import logging
from uuid import UUID

from app.aioRedis import get_thread_safe_redis
from app.core.quota_manager import get_end_user_memory_limit_async
from app.db import get_async_db_context
from app.repositories.end_user_repository import EndUserRepository, get_tenant_id_by_end_user_id_async
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.graph_search import forget_count_active_nodes
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

_logger = logging.getLogger(__name__)
_LOG_PREFIX = "[MEMORY_COUNT_SYNC]"


async def sync_end_user_memory_count_from_neo4j(
        end_user_id: str,
        connector: Neo4jConnector,
) -> int:
    """
    Sync one end user's Neo4j memory node count to PostgreSQL.

    The caller owns the Neo4j connector lifecycle.
    """
    if not end_user_id:
        return 0

    result = await connector.execute_query(
        MemoryConfigRepository.SEARCH_FOR_ALL_BATCH,
        end_user_ids=[end_user_id],
    )
    node_count = int(result[0]["total"]) if result else 0

    memory_node_count = await forget_count_active_nodes(connector, end_user_id)
    async with get_async_db_context() as db:
        await EndUserRepository(db).update_memory_count_async(UUID(end_user_id), node_count)
        tenant_id = await get_tenant_id_by_end_user_id_async(db, end_user_id)
        memory_limit = await get_end_user_memory_limit_async(db, tenant_id)
    redis_client = get_thread_safe_redis()
    if redis_client:
        if memory_node_count > memory_limit:
            await redis_client.sadd("forget:candidates", end_user_id)
            _logger.info(
                f"{_LOG_PREFIX} 加入遗忘候选: end_user_id={end_user_id}, "
                f"count={memory_node_count}, limit={memory_limit}"
            )
        else:
            await redis_client.srem("forget:candidates", end_user_id)

    _logger.info(f"{_LOG_PREFIX} 同步完成: end_user_id={end_user_id}, count={node_count}")
    return node_count


def sync_memory_count_neo4j(end_user_id: str) -> None:
    """
    Synchronous wrapper for use in Celery tasks and other sync contexts.

    Uses asyncio.run() which creates a fresh event loop, runs the coroutine,
    and closes the loop automatically — no resource leaks.
    """

    async def _run():
        connector = Neo4jConnector()
        try:
            await sync_end_user_memory_count_from_neo4j(end_user_id, connector)
        finally:
            await connector.close()

    try:
        asyncio.run(_run())
    except Exception as exc:
        _logger.warning(
            f"{_LOG_PREFIX} 同步失败（不影响主流程）: end_user_id={end_user_id}, error={exc}",
            exc_info=True,
        )
