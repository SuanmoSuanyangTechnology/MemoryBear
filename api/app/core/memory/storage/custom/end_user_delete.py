from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

logger = logging.getLogger(__name__)

DEFAULT_DELETE_BATCH_SIZE = 1000

END_USER_DELETE_BATCH_WITH_IDENTITIES = """
MATCH (n {end_user_id: $end_user_id})
WITH n, [
    label IN $supported_labels
    WHERE label IN labels(n)
] AS memory_labels,
     elementId(n) AS element_id
ORDER BY element_id
LIMIT $batch_size
WITH n,
     element_id,
     CASE
         WHEN size(memory_labels) = 1
              AND n.id IS NOT NULL
              AND trim(toString(n.id)) <> ''
         THEN toString(n.id)
         ELSE null
     END AS node_id,
     CASE
         WHEN size(memory_labels) = 1
              AND n.id IS NOT NULL
              AND trim(toString(n.id)) <> ''
         THEN head(memory_labels)
         ELSE null
     END AS label,
     size(memory_labels) = 1
         AND n.id IS NOT NULL
         AND trim(toString(n.id)) <> '' AS projectable
DETACH DELETE n
RETURN element_id, node_id, label, projectable
ORDER BY element_id
"""

END_USER_DELETE_REMAINING_COUNT = """
MATCH (n {end_user_id: $end_user_id})
RETURN count(n) AS remaining_nodes
"""

DELETE_END_USER_DANGLING_RELATIONSHIPS = """
MATCH ()-[r]->()
WHERE r.end_user_id = $end_user_id
DELETE r
RETURN count(r) AS deleted_relationships
"""


@dataclass(frozen=True, slots=True)
class DeletedEndUserNodeIdentity:
    element_id: str
    node_id: str
    label: MemoryNodeType


@dataclass(frozen=True, slots=True)
class _DeletedEndUserBatch:
    deleted_count: int
    affected_nodes: tuple[DeletedEndUserNodeIdentity, ...]
    element_ids: frozenset[str]
    identities: frozenset[tuple[MemoryNodeType, str]]


class EndUserDeleteOutboxError(OutboxEnqueueError):
    """Outbox failed after one end-user deletion batch committed in Neo4j."""

    def __init__(
        self,
        cause: OutboxEnqueueError,
        affected_nodes: list[DeletedEndUserNodeIdentity],
        *,
        deleted_count: int,
        published_count: int,
    ) -> None:
        super().__init__(list(cause.event_ids), cause.reason)
        self.affected_nodes = tuple(affected_nodes)
        self.deleted_count = deleted_count
        self.published_count = published_count


def _parse_deleted_batch(
    rows: list[dict],
    *,
    batch_size: int,
    seen_element_ids: set[str] | frozenset[str],
    seen_identities: set[tuple[MemoryNodeType, str]] | frozenset[tuple[MemoryNodeType, str]],
) -> _DeletedEndUserBatch:
    """Validate all deleted rows and project only supported node identities."""
    if len(rows) > batch_size:
        raise RuntimeError("End-user delete returned more nodes than its batch size")

    affected: list[DeletedEndUserNodeIdentity] = []
    element_ids: set[str] = set()
    identities: set[tuple[MemoryNodeType, str]] = set()
    for row in rows:
        element_id = str(row.get("element_id") or "")
        if not element_id:
            raise RuntimeError("End-user delete returned a blank element ID")
        if element_id in element_ids or element_id in seen_element_ids:
            raise RuntimeError("End-user delete returned duplicate element IDs")
        element_ids.add(element_id)

        projectable = row.get("projectable")
        if not isinstance(projectable, bool):
            raise RuntimeError("End-user delete returned an invalid projection marker")
        if not projectable:
            if row.get("node_id") is not None or row.get("label") is not None:
                raise RuntimeError(
                    "End-user delete returned identity fields for an unprojectable node"
                )
            continue

        node_id = str(row.get("node_id") or "")
        if not node_id:
            raise RuntimeError("End-user delete returned a blank business ID")
        try:
            label = MemoryNodeType(row.get("label"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "End-user delete returned an unsupported node label"
            ) from exc

        identity = (label, node_id)
        if identity in identities or identity in seen_identities:
            raise RuntimeError("End-user delete returned duplicate node identities")
        identities.add(identity)
        affected.append(
            DeletedEndUserNodeIdentity(
                element_id=element_id,
                node_id=node_id,
                label=label,
            )
        )

    return _DeletedEndUserBatch(
        deleted_count=len(rows),
        affected_nodes=tuple(affected),
        element_ids=frozenset(element_ids),
        identities=frozenset(identities),
    )


async def delete_end_user_memory_nodes(
    end_user_id: str,
    *,
    batch_size: int = DEFAULT_DELETE_BATCH_SIZE,
    client: Neo4jClient | None = None,
    outbox_repository: OutboxRepository | None = None,
) -> int:
    """Delete every node owned by one end user and publish exact DELETE events.

    The legacy operation unconditionally removed every ``end_user_id`` node.
    Preserve that behavior: nodes without exactly one supported memory label or
    a business ``id`` are still deleted, but cannot produce projection events.
    Each batch validates all returned element IDs and any projectable identity
    inside the managed write transaction before commit.
    """
    if not isinstance(end_user_id, str) or not end_user_id.strip():
        raise ValueError("end_user_id must not be blank")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    owns_client = client is None
    if client is None:
        client = await Neo4jClient.create()

    deleted_count = 0
    published_count = 0
    seen_element_ids: set[str] = set()
    seen_identities: set[tuple[MemoryNodeType, str]] = set()
    supported_labels = [label.value for label in MemoryNodeType]
    try:
        while True:
            seen_element_snapshot = frozenset(seen_element_ids)
            seen_identity_snapshot = frozenset(seen_identities)
            batch = await client.execute_validated_write_query(
                END_USER_DELETE_BATCH_WITH_IDENTITIES,
                lambda rows: _parse_deleted_batch(
                    rows,
                    batch_size=batch_size,
                    seen_element_ids=seen_element_snapshot,
                    seen_identities=seen_identity_snapshot,
                ),
                end_user_id=end_user_id,
                batch_size=batch_size,
                supported_labels=supported_labels,
            )
            seen_element_ids.update(batch.element_ids)
            seen_identities.update(batch.identities)
            if batch.deleted_count == 0:
                break

            deleted_count += batch.deleted_count
            affected = list(batch.affected_nodes)
            if not affected:
                continue

            events = [
                OutboxEventInput(
                    label=node.label,
                    node_id=node.node_id,
                    operation=OutboxOperation.DELETE,
                )
                for node in affected
            ]
            try:
                await enqueue_events(events, repository=outbox_repository)
            except OutboxEnqueueError as exc:
                raise EndUserDeleteOutboxError(
                    exc,
                    affected,
                    deleted_count=deleted_count,
                    published_count=published_count,
                ) from None
            published_count += len(affected)

        remaining_rows = await client.execute_query(
            END_USER_DELETE_REMAINING_COUNT,
            end_user_id=end_user_id,
        )
        remaining = (
            int(remaining_rows[0].get("remaining_nodes", 0) or 0)
            if remaining_rows
            else 0
        )
        if remaining != 0:
            raise RuntimeError(
                "End-user graph changed during deletion; memory nodes remain"
            )

        # DETACH DELETE removes relationships attached to deleted nodes. Keep
        # legacy cleanup semantics for any relationship carrying this user ID
        # whose endpoints did not belong to the deleted node set.
        await client.execute_query(
            DELETE_END_USER_DANGLING_RELATIONSHIPS,
            end_user_id=end_user_id,
        )
        return deleted_count
    finally:
        if owns_client:
            active_error = sys.exc_info()[0] is not None
            try:
                await client.close()
            except Exception:
                if not active_error:
                    raise
                logger.exception(
                    "Failed to close Neo4j client after end-user delete failure"
                )