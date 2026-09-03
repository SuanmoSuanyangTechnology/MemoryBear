from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.memory.storage.custom.automatic_forgetting import (
    FORGETTABLE_NODE_TYPES,
)
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import FilterCondition, NodeFilter
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage.service import MemoryStorageService, get_storage_service


FORGET_RECOVERY_TARGET_QUERY = """
MATCH (n)
WHERE elementId(n) = $element_id
  AND n.end_user_id = $end_user_id
  AND any(label IN labels(n) WHERE label IN $supported_labels)
  AND n.id IS NOT NULL
RETURN elementId(n) AS element_id,
       toString(n.id) AS node_id,
       labels(n) AS labels,
       n.delete_at IS NOT NULL AS is_forgotten
"""


@dataclass(frozen=True, slots=True)
class ForgetRecoveryTarget:
    element_id: str
    node_id: str
    label: MemoryNodeType
    recovered_now: bool


async def _resolve_forget_recovery_target(
    client: Neo4jClient,
    element_id: str,
    end_user_id: str,
) -> ForgetRecoveryTarget | None:
    rows = await client.execute_query(
        FORGET_RECOVERY_TARGET_QUERY,
        element_id=element_id,
        end_user_id=end_user_id,
        supported_labels=[label.value for label in FORGETTABLE_NODE_TYPES],
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("elementId resolved to multiple memory nodes")

    row: dict[str, Any] = rows[0]
    canonical_labels: list[MemoryNodeType] = []
    for raw_label in row.get("labels", []):
        try:
            label = MemoryNodeType(raw_label)
        except ValueError:
            continue
        if label in FORGETTABLE_NODE_TYPES:
            canonical_labels.append(label)
    if len(canonical_labels) != 1:
        raise ValueError(
            "forgotten node must have exactly one supported storage label"
        )

    node_id = row.get("node_id")
    if node_id is None or not str(node_id).strip():
        raise ValueError("forgotten node is missing its business id")

    return ForgetRecoveryTarget(
        element_id=str(row.get("element_id") or element_id),
        node_id=str(node_id),
        label=canonical_labels[0],
        recovered_now=bool(row.get("is_forgotten")),
    )


async def resolve_forget_recovery_target(
    element_id: str,
    end_user_id: str,
    *,
    client: Neo4jClient | None = None,
) -> ForgetRecoveryTarget | None:
    """Resolve the authoritative identity and current state of a forget audit."""
    owns_client = client is None
    if client is None:
        client = await Neo4jClient.create()
    try:
        return await _resolve_forget_recovery_target(
            client,
            element_id,
            end_user_id,
        )
    finally:
        if owns_client:
            await client.close()


async def recover_forgotten_node_by_element_id(
    element_id: str,
    end_user_id: str,
    *,
    client: Neo4jClient | None = None,
    storage_service: MemoryStorageService | None = None,
) -> ForgetRecoveryTarget | None:
    """Idempotently restore a forgotten node through the storage write router.

    The mutation always clears ``delete_at`` through ``update_node`` so an
    idempotent retry republishes the UPSERT projection event after a possible
    prior Outbox failure. ``recovered_now`` still records whether this call
    observed the node as forgotten before the mutation, allowing PostgreSQL
    audit reconciliation without refreshing unrelated access fields.
    """
    owns_client = client is None
    if client is None:
        client = await Neo4jClient.create()

    try:
        target = await _resolve_forget_recovery_target(
            client,
            element_id,
            end_user_id,
        )
        if target is None:
            return None

        service = storage_service or get_storage_service()
        result = await service.update_node(
            target.label,
            {"delete_at": None},
            NodeFilter.all_of(
                FilterCondition(field="id", value=target.node_id),
                FilterCondition(field="end_user_id", value=end_user_id),
            ),
        )
        if result.affected_count == 1 and result.ids == [target.node_id]:
            return target
        if result.affected_count != 0:
            raise RuntimeError(
                "Forgotten node recovery returned an unexpected identity"
            )

        # Another recovery may have won between resolution and mutation. Read
        # Neo4j again so that an idempotent retry can still reconcile the audit.
        current = await _resolve_forget_recovery_target(
            client,
            element_id,
            end_user_id,
        )
        if current is None:
            return None
        if (
            current.node_id != target.node_id
            or current.label != target.label
        ):
            raise RuntimeError("Forgotten node identity changed during recovery")
        if current.recovered_now:
            raise RuntimeError("Forgotten node recovery did not update the node")
        return current
    finally:
        if owns_client:
            await client.close()
