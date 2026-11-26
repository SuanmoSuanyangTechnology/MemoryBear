"""Graph write for ``POST /end_user/info/update``'s alias side effect.

The API performs this write through ``storage/custom/node_queries.py::
update_user_entity_aliases``: one property update on the end user's own
``ExtractedEntity`` node (the node whose ``name`` is one of the user-entity
aliases), followed by one outbox event per affected node so memory-jobs projects
the change into Elasticsearch.

This service reaches the same effect through its write-capable graph client
(:class:`Neo4jGraphWriter`) instead of the read-only storage provider, which is
also how the end-user merge performs its graph mutations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.common.timing import utcnow_naive
from src.infrastructure.graph.client import Neo4jGraphWriter
from src.repositories.outbox import (
    OutboxEventInput,
    OutboxOperation,
    OutboxRepository,
)
from src.storage.enums import MemoryNodeType

logger = logging.getLogger(__name__)

#: Node names the API treats as "the end user's own entity" (host parity with
#: ``node_queries.update_user_entity_aliases``).
USER_ENTITY_NAMES = ("用户", "我", "User", "I")

_UPDATE_USER_ALIASES = """
MATCH (n:ExtractedEntity)
WHERE n.end_user_id = $end_user_id
  AND n.name IN $names
SET n.aliases = $aliases,
    n.updated_at = $updated_at
RETURN toString(n.id) AS node_id
"""


@dataclass(frozen=True, slots=True)
class AliasUpdateResult:
    """How many user-entity nodes took the new alias list."""

    affected_count: int = 0
    outbox_events: int = 0


async def update_user_entity_aliases(
    end_user_id: str,
    aliases: list[str],
    *,
    client: Neo4jGraphWriter,
    outbox_repository: OutboxRepository,
    names: tuple[str, ...] = USER_ENTITY_NAMES,
) -> AliasUpdateResult:
    """Replace the alias list on the end user's own entity node(s).

    A blank alias list still writes: the API's update is destructive by design,
    so clearing the aliases in ``end_user_info`` must clear them in the graph
    too. A node with no alias change keeps the same value, so the write stays
    idempotent without a pre-read.
    """

    if not end_user_id:
        return AliasUpdateResult()

    now = utcnow_naive().isoformat(sep=" ")

    async def _update(tx: Any) -> list[str]:
        result = await tx.run(
            _UPDATE_USER_ALIASES,
            end_user_id=end_user_id,
            names=list(names),
            aliases=list(aliases),
            updated_at=now,
        )
        return [record["node_id"] async for record in result]

    node_ids = await client.execute_write_transaction(_update)
    if not node_ids:
        logger.info(
            "[end_user_aliases] no user-entity node for end_user=%s; alias list not written",
            end_user_id,
        )
        return AliasUpdateResult()

    events = [
        OutboxEventInput(
            label=MemoryNodeType.EXTRACTED_ENTITY,
            node_id=node_id,
            operation=OutboxOperation.UPSERT,
        )
        for node_id in node_ids
    ]
    await outbox_repository.enqueue_many(events)
    logger.info(
        "[end_user_aliases] synced aliases for end_user=%s: nodes=%s",
        end_user_id,
        len(node_ids),
    )
    return AliasUpdateResult(affected_count=len(node_ids), outbox_events=len(events))


__all__ = [
    "USER_ENTITY_NAMES",
    "AliasUpdateResult",
    "update_user_entity_aliases",
]
