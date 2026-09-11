from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.memory.models.service_models import ForgetLog
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import FilterCondition, NodeFilter
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage.service import MemoryStorageService, get_storage_service
from app.core.utils.datetime_utils import utcnow
from app.db import get_db_context
from app.models.memory_forget_model import ForgetTrigger
from app.repositories.forget_log_repository import ForgetLogRepository

logger = logging.getLogger(__name__)


MANUAL_DELETE_TARGET_QUERY = """
MATCH (n)
WHERE elementId(n) = $element_id
  AND n.end_user_id = $end_user_id
  AND any(label IN labels(n) WHERE label IN $supported_labels)
RETURN elementId(n) AS element_id,
       n.id AS node_id,
       labels(n) AS labels,
       n.content AS content,
       n.statement AS statement,
       n.text AS text,
       n.name AS name
"""


@dataclass(frozen=True, slots=True)
class ManualDeleteTarget:
    element_id: str
    node_id: str
    label: MemoryNodeType
    content: str


async def resolve_manual_delete_target(
    element_id: str,
    end_user_id: str,
    *,
    client: Neo4jClient | None = None,
) -> ManualDeleteTarget | None:
    """Resolve a Neo4j element ID without mutating the node."""
    owns_client = client is None
    if client is None:
        client = await Neo4jClient.create()

    try:
        rows = await client.execute_query(
            MANUAL_DELETE_TARGET_QUERY,
            element_id=element_id,
            end_user_id=end_user_id,
            supported_labels=[label.value for label in MemoryNodeType],
        )
    finally:
        if owns_client:
            await client.close()

    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("elementId resolved to multiple memory nodes")

    row: dict[str, Any] = rows[0]
    canonical_labels: list[MemoryNodeType] = []
    for raw_label in row.get("labels", []):
        try:
            canonical_labels.append(MemoryNodeType(raw_label))
        except ValueError:
            continue
    if len(canonical_labels) != 1:
        raise ValueError("memory node must have exactly one supported storage label")

    node_id = row.get("node_id")
    if node_id is None or not str(node_id).strip():
        raise ValueError("memory node is missing its business id")

    content = (
        row.get("content")
        or row.get("statement")
        or row.get("text")
        or row.get("name")
        or ""
    )
    return ManualDeleteTarget(
        element_id=str(row.get("element_id") or element_id),
        node_id=str(node_id),
        label=canonical_labels[0],
        content=str(content),
    )


def _write_manual_delete_audit(
    target: ManualDeleteTarget,
    end_user_id: str,
    operator: uuid.UUID,
) -> None:
    log = ForgetLog(
        node_id=target.element_id,
        end_user_id=uuid.UUID(end_user_id),
        node_type=target.label.value,
        content=target.content,
        trigger=ForgetTrigger.Manual.value,
        reason="manual",
        recoverable=False,
        operator=operator,
        delete_at=utcnow(),
        is_recovered=False,
    )
    with get_db_context() as db:
        ForgetLogRepository.sync_logs(db, [log])
        db.commit()


async def delete_manual_node_by_element_id(
    element_id: str,
    end_user_id: str,
    operator: uuid.UUID,
    *,
    client: Neo4jClient | None = None,
    storage_service: MemoryStorageService | None = None,
) -> bool:
    """Resolve, physically delete, enqueue DELETE, and persist the audit.

    Resolution is read-only. The mutation is constrained again by business ID
    and ``end_user_id`` and routed through ``MemoryStorageService`` so that
    ``WriteRouter`` publishes the DELETE event from the mutation's returned ID.
    """
    target = await resolve_manual_delete_target(
        element_id,
        end_user_id,
        client=client,
    )
    if target is None:
        return False

    node_filter = NodeFilter.all_of(
        FilterCondition(field="id", value=target.node_id),
        FilterCondition(field="end_user_id", value=end_user_id),
    )
    service = storage_service or get_storage_service()
    try:
        result = await service.delete_node(
            target.label,
            node_filter,
            draft=False,
        )
    except OutboxEnqueueError:
        # WriteRouter enqueues only after Neo4j commits. Preserve the business
        # audit for the committed deletion, then retain the original failure.
        try:
            _write_manual_delete_audit(target, end_user_id, operator)
        except Exception:
            logger.exception(
                "Failed to persist manual-delete audit after Outbox failure: "
                "element_id=%s end_user_id=%s",
                element_id,
                end_user_id,
            )
        raise

    if result.affected_count == 0:
        # The node changed or disappeared after resolution. The ownership-
        # constrained mutation did nothing, so no Outbox event or audit exists.
        return False
    if result.affected_count != 1 or result.ids != [target.node_id]:
        raise RuntimeError(
            "Manual node delete returned an unexpected affected identity"
        )

    _write_manual_delete_audit(target, end_user_id, operator)
    return True
