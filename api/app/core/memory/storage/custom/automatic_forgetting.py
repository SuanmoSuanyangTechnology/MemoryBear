from __future__ import annotations

from dataclasses import dataclass

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.neo4j.client import Neo4jClient


FORGETTABLE_NODE_TYPES = (
    MemoryNodeType.STATEMENT,
    MemoryNodeType.CHUNK,
    MemoryNodeType.EXTRACTED_ENTITY,
    MemoryNodeType.MEMORY_SUMMARY,
    MemoryNodeType.DIALOGUE,
)

FORGET_SOFT_DELETE_WITH_IDENTITIES = """
MATCH (n {end_user_id: $end_user_id})
WHERE n.delete_at IS NULL
  AND elementId(n) IN $element_ids
  AND any(label IN labels(n) WHERE label IN $supported_labels)
  AND n.id IS NOT NULL
  AND (NOT n:Statement OR coalesce(n.is_permanent, false) = false)
  AND (NOT n:ExtractedEntity OR (
      coalesce(n.extraction_count, 0) < $protection_threshold
      AND n.name <> '用户'
  ))
  AND (
      NOT (n:Statement OR n:Chunk OR n:ExtractedEntity) OR (
          n.topology_score IS NOT NULL
          AND (
              toFloat(n.topology_score) IS NULL
              OR isNaN(toFloat(n.topology_score))
              OR toFloat(n.topology_score) < 1.0
          )
      )
  )
  AND (
      NOT $require_isolated OR (
          (n:Statement OR n:Chunk OR n:ExtractedEntity)
          AND NOT EXISTS {
              MATCH (n)--(related)
              WHERE related <> n
                AND related.end_user_id = $end_user_id
                AND related.delete_at IS NULL
          }
      )
  )
WITH n,
     elementId(n) AS element_id,
     toString(n.id) AS node_id,
     head([
         label IN $supported_labels
         WHERE label IN labels(n)
     ]) AS label
SET n.delete_at = datetime($now)
RETURN element_id, node_id, label
ORDER BY element_id
"""


@dataclass(frozen=True, slots=True)
class ForgottenNodeIdentity:
    element_id: str
    node_id: str
    label: MemoryNodeType


class AutomaticForgetOutboxError(OutboxEnqueueError):
    """Outbox failed after Neo4j committed automatic soft deletes."""

    def __init__(
        self,
        cause: OutboxEnqueueError,
        affected_nodes: list[ForgottenNodeIdentity],
    ) -> None:
        super().__init__(list(cause.event_ids), cause.reason)
        self.affected_nodes = tuple(affected_nodes)


async def soft_delete_forgetting_nodes(
    end_user_id: str,
    element_ids: list[str],
    now: str,
    *,
    protection_threshold: int,
    require_isolated: bool = False,
    client: Neo4jClient | None = None,
    outbox_repository: OutboxRepository | None = None,
) -> list[ForgottenNodeIdentity]:
    """Soft-delete eligible memory nodes and publish exact DRAFT_DELETE events.

    Node identities are returned by the same Cypher mutation that sets
    ``delete_at``. An Outbox failure therefore occurs after the primary Neo4j
    commit and carries those identities for the caller's audit compensation.
    """
    if not element_ids:
        return []

    owns_client = client is None
    if client is None:
        client = await Neo4jClient.create()

    try:
        rows = await client.execute_query(
            FORGET_SOFT_DELETE_WITH_IDENTITIES,
            end_user_id=end_user_id,
            element_ids=element_ids,
            now=now,
            protection_threshold=protection_threshold,
            require_isolated=require_isolated,
            supported_labels=[label.value for label in FORGETTABLE_NODE_TYPES],
        )
    finally:
        if owns_client:
            await client.close()

    affected_nodes = [
        ForgottenNodeIdentity(
            element_id=str(row["element_id"]),
            node_id=str(row["node_id"]),
            label=MemoryNodeType(row["label"]),
        )
        for row in rows
    ]
    unique_element_ids = {node.element_id for node in affected_nodes}
    unique_node_ids = {(node.label, node.node_id) for node in affected_nodes}
    if len(unique_element_ids) != len(affected_nodes):
        raise RuntimeError("Automatic forget returned duplicate element IDs")
    if len(unique_node_ids) != len(affected_nodes):
        raise RuntimeError("Automatic forget returned duplicate node identities")

    events = [
        OutboxEventInput(
            label=node.label,
            node_id=node.node_id,
            operation=OutboxOperation.DRAFT_DELETE,
        )
        for node in affected_nodes
    ]
    try:
        await enqueue_events(events, repository=outbox_repository)
    except OutboxEnqueueError as exc:
        raise AutomaticForgetOutboxError(exc, affected_nodes) from None

    return affected_nodes
