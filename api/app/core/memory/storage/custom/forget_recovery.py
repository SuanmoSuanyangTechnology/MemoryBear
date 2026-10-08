from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.memory.storage.custom.automatic_forgetting import (
    FORGETTABLE_NODE_TYPES,
)
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import (
    FilterCondition,
    NodeFilter,
    NodeProjection,
)
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage.service import MemoryStorageService, get_storage_service
from app.core.memory.storage_services.reembedding_engine.spec import (
    reembed_target_for,
)

logger = logging.getLogger(__name__)


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


async def _reembed_recovered_node(
        *,
        client: Neo4jClient,
        storage_service: MemoryStorageService,
        target: ForgetRecoveryTarget,
        end_user_id: str,
        embedder,
) -> None:
    """Recompute a recovered node's vector with the workspace's current model.

    Best-effort: ``delete_at`` is already cleared, so an embedding failure must
    not fail the recovery — it only leaves the node recalling with its previous
    model's vector until the next bulk rebuild.
    """
    if embedder is None:
        return
    spec = reembed_target_for(target.label)
    if spec is None:
        return
    try:
        # 从 Neo4j（权威）读源文本，而不是走读路由命中可能滞后的 ES 投影。
        read = await client.get_node(
            target.label,
            NodeFilter.all_of(
                FilterCondition(field="id", value=target.node_id),
                FilterCondition(field="end_user_id", value=end_user_id),
            ),
            NodeProjection.of("id", spec.text_field),
        )
        if not read.items:
            return
        text = read.items[0].data.get(spec.text_field)
        if not isinstance(text, str) or not text.strip():
            return
        vectors = await embedder.aembed_documents([text])
        if not vectors or vectors[0] is None:
            return
        await storage_service.update_node_embeddings(
            target.label,
            spec.vector_field,
            [(target.node_id, vectors[0])],
        )
    except Exception as exc:
        logger.error(
            "memory recover re-embed failed: end_user=%s label=%s node=%s "
            "error=%s",
            end_user_id,
            target.label.value,
            target.node_id,
            exc,
            exc_info=True,
        )


async def recover_forgotten_node_by_element_id(
    element_id: str,
    end_user_id: str,
    *,
    client: Neo4jClient | None = None,
    storage_service: MemoryStorageService | None = None,
    embedder=None,
) -> ForgetRecoveryTarget | None:
    """Idempotently restore a forgotten node through the storage write router.

    The mutation always clears ``delete_at`` through ``update_node`` so an
    idempotent retry republishes the UPSERT projection event after a possible
    prior Outbox failure. ``recovered_now`` still records whether this call
    observed the node as forgotten before the mutation, allowing PostgreSQL
    audit reconciliation without refreshing unrelated access fields.

    When ``embedder`` is provided, the node's vector is also recomputed with the
    current model after ``delete_at`` is cleared (best-effort), since a bulk
    rebuild skips soft-deleted nodes.
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
            await _reembed_recovered_node(
                client=client,
                storage_service=service,
                target=target,
                end_user_id=end_user_id,
                embedder=embedder,
            )
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
        await _reembed_recovered_node(
            client=client,
            storage_service=service,
            target=current,
            end_user_id=end_user_id,
            embedder=embedder,
        )
        return current
    finally:
        if owns_client:
            await client.close()
