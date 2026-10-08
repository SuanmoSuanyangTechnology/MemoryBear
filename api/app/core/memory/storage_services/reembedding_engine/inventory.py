from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage_services.reembedding_engine.spec import (
    REEMBED_TARGETS,
    ReembedTarget,
)

logger = logging.getLogger(__name__)

INVENTORY_CHUNK_SIZE = 5_000

_INVENTORY_QUERY = """
MATCH (n:`{label}`)
WHERE n.end_user_id IN $end_user_ids
  AND n.id IS NOT NULL
  AND n.delete_at IS NULL
  AND n.`{text_field}` IS NOT NULL
  AND trim(n.`{text_field}`) <> ''
RETURN n.end_user_id AS end_user_id, count(n) AS total
"""


def build_inventory_query(target: ReembedTarget) -> str:
    """Cypher counting one label's embeddable nodes per end_user."""
    return _INVENTORY_QUERY.format(
        label=target.label.value,
        text_field=target.text_field,
    )


def _chunks(values: Sequence[str]) -> list[list[str]]:
    return [
        list(values[start:start + INVENTORY_CHUNK_SIZE])
        for start in range(0, len(values), INVENTORY_CHUNK_SIZE)
    ]


async def inventory_end_users(
        end_user_ids: Sequence[str],
        *,
        client: Neo4jClient | None = None,
        targets: tuple[ReembedTarget, ...] = REEMBED_TARGETS,
) -> dict[str, int]:
    """Count embeddable nodes per end_user across every re-embed target."""
    if not end_user_ids:
        return {}

    owns_client = client is None
    if client is None:
        client = await Neo4jClient.create()

    counts: dict[str, int] = {}
    try:
        for target in targets:
            query = build_inventory_query(target)
            for chunk in _chunks(list(end_user_ids)):
                rows = await client.execute_query(
                    query,
                    end_user_ids=chunk,
                )
                for row in rows or []:
                    end_user_id = row.get("end_user_id")
                    total = int(row.get("total", 0) or 0)
                    if end_user_id is None or total <= 0:
                        continue
                    counts[str(end_user_id)] = (
                        counts.get(str(end_user_id), 0) + total
                    )
    finally:
        if owns_client:
            await client.close()

    logger.info(
        "[MemoryReembed] inventory done: end_users=%s with_nodes=%s nodes=%s",
        len(end_user_ids),
        len(counts),
        sum(counts.values()),
    )
    return counts


__all__ = [
    "INVENTORY_CHUNK_SIZE",
    "build_inventory_query",
    "inventory_end_users",
]
