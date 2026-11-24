"""Count a graph end user's live memory nodes for the ``memory_count`` cache.

Graph mode has no independent index to aggregate: the API counts Neo4j nodes
with ``delete_at IS NULL`` after a merge and writes the number back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.storage.provider.neo4j.client import Neo4jClient, configure_neo4j_manager

COUNT_END_USER_NODES = """
MATCH (n) WHERE n.end_user_id = $end_user_id AND n.delete_at is NULL
RETURN count(n) AS total
"""


async def count_graph_nodes(client: Neo4jClient, end_user_id: object) -> int:
    rows = await client.execute_query(
        COUNT_END_USER_NODES, end_user_id=str(end_user_id)
    )
    return int(rows[0]["total"]) if rows else 0


def build_graph_memory_count(
    neo4j_manager: Any,
) -> Callable[[str], Awaitable[int]]:
    configure_neo4j_manager(neo4j_manager)
    client: Neo4jClient | None = None

    async def _count(end_user_id: str) -> int:
        nonlocal client
        if client is None:
            client = await Neo4jClient.create()
        return await count_graph_nodes(client, end_user_id)

    return _count


__all__ = ["COUNT_END_USER_NODES", "build_graph_memory_count", "count_graph_nodes"]