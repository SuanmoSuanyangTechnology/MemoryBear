from uuid import UUID

from app.db import get_db_context
from app.models.end_user_model import EndUser
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


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

    return node_count
