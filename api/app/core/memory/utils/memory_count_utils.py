import asyncio
import logging
from uuid import UUID

from app.db import get_db_context
from app.models.end_user_model import EndUser
from app.repositories.memory_config_repository import MemoryConfigRepository
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

    with get_db_context() as db:
        db.query(EndUser).filter(
            EndUser.id == UUID(end_user_id)
        ).update(
            {"memory_count": node_count},
            synchronize_session=False,
        )
        db.commit()

    _logger.info(f"{_LOG_PREFIX} 同步完成: end_user_id={end_user_id}, count={node_count}")
    return node_count


async def _async_sync_memory_count_neo4j(
    end_user_id: str,
    connector: Neo4jConnector | None = None,
) -> int:
    """
    Async helper: sync memory count, optionally managing the connector lifecycle.

    If *connector* is None, a new Neo4jConnector is created and closed internally.
    If *connector* is provided, the caller is responsible for closing it.
    """
    owned = connector is None
    if owned:
        connector = Neo4jConnector()
    try:
        return await sync_end_user_memory_count_from_neo4j(end_user_id, connector)
    except Exception as exc:
        _logger.warning(
            f"{_LOG_PREFIX} 同步失败（不影响主流程）: end_user_id={end_user_id}, error={exc}"
        )
        return 0
    finally:
        if owned:
            await connector.close()


def sync_memory_count_neo4j(end_user_id: str) -> None:
    """
    Synchronous wrapper for use in Celery tasks and other sync contexts.

    Reuses the current event loop if available and open; otherwise creates a
    new one.  All exceptions are caught and logged so callers never need an
    extra try/except just for logging.
    """
    try:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(_async_sync_memory_count_neo4j(end_user_id))
    except Exception as exc:
        _logger.warning(
            f"{_LOG_PREFIX} 同步失败（不影响主流程）: end_user_id={end_user_id}, error={exc}"
        )
