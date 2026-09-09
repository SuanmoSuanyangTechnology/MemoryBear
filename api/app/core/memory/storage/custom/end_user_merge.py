from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

logger = logging.getLogger(__name__)

END_USER_MERGE_SOURCE_SNAPSHOT = """
MATCH (n {end_user_id: $source_id})
RETURN elementId(n) AS element_id,
       toString(n.id) AS node_id,
       [label IN $supported_labels WHERE label IN labels(n)] AS memory_labels,
       properties(n) AS properties
ORDER BY element_id
"""

END_USER_MERGE_TARGET_USER_SNAPSHOT = """
MATCH (n:ExtractedEntity {end_user_id: $target_id})
WHERE n.name = '用户'
RETURN elementId(n) AS element_id,
       toString(n.id) AS node_id,
       [label IN $supported_labels WHERE label IN labels(n)] AS memory_labels,
       properties(n) AS properties
ORDER BY element_id
"""

END_USER_MERGE_UPDATE_TARGET_USER = """
MATCH (n:ExtractedEntity)
WHERE elementId(n) = $element_id
  AND n.end_user_id = $target_id
SET n += $merged_properties
RETURN elementId(n) AS element_id,
       toString(n.id) AS node_id,
       'ExtractedEntity' AS label
"""

END_USER_MERGE_REDIRECT_INCOMING = """
MATCH (src:ExtractedEntity)
WHERE elementId(src) = $source_element_id
MATCH (tgt:ExtractedEntity)
WHERE elementId(tgt) = $target_element_id
WITH src, tgt
MATCH (src)<-[r_in]-(other)
WHERE other <> tgt
WITH other, tgt, r_in
CALL apoc.create.relationship(other, type(r_in), properties(r_in), tgt)
YIELD rel AS r_new
SET r_new.end_user_id = $target_id
DELETE r_in
RETURN count(r_new) AS redirected
"""

END_USER_MERGE_REDIRECT_OUTGOING = """
MATCH (src:ExtractedEntity)
WHERE elementId(src) = $source_element_id
MATCH (tgt:ExtractedEntity)
WHERE elementId(tgt) = $target_element_id
WITH src, tgt
MATCH (src)-[r_out]->(other)
WHERE other <> tgt
WITH other, tgt, r_out
CALL apoc.create.relationship(tgt, type(r_out), properties(r_out), other)
YIELD rel AS r_new
SET r_new.end_user_id = $target_id
DELETE r_out
RETURN count(r_new) AS redirected
"""

END_USER_MERGE_DELETE_SOURCE_USER = """
MATCH (n:ExtractedEntity)
WHERE elementId(n) = $element_id
  AND n.end_user_id = $source_id
WITH n, elementId(n) AS element_id, toString(n.id) AS node_id
DETACH DELETE n
RETURN element_id, node_id, 'ExtractedEntity' AS label
"""

END_USER_MERGE_REASSIGN_NODES_WITH_IDENTITIES = """
MATCH (n {end_user_id: $source_id})
WITH n,
     elementId(n) AS element_id,
     toString(n.id) AS node_id,
     head([
         label IN $supported_labels
         WHERE label IN labels(n)
     ]) AS label
SET n.end_user_id = $target_id
RETURN element_id, node_id, label
ORDER BY element_id
"""

END_USER_MERGE_REASSIGN_RELATIONSHIPS = """
MATCH ()-[r]->()
WHERE r.end_user_id = $source_id
SET r.end_user_id = $target_id
RETURN count(r) AS updated_edges
"""


@dataclass(frozen=True, slots=True)
class EndUserMergeNodeIdentity:
    element_id: str
    node_id: str
    label: MemoryNodeType
    operation: OutboxOperation


@dataclass(frozen=True, slots=True)
class EndUserMergeStats:
    sources_merged: int = 0
    reassigned_nodes: int = 0
    reassigned_edges: int = 0
    outbox_events: int = 0


@dataclass(frozen=True, slots=True)
class _SnapshotNode:
    element_id: str
    node_id: str
    label: MemoryNodeType
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _SourceMergeResult:
    affected_nodes: tuple[EndUserMergeNodeIdentity, ...]
    reassigned_nodes: int
    reassigned_edges: int


class EndUserMergePrimaryError(RuntimeError):
    """A source transaction failed after zero or more prior sources completed."""

    def __init__(
        self,
        *,
        source_id: str,
        completed_sources: tuple[str, ...],
    ) -> None:
        self.source_id = source_id
        self.completed_sources = completed_sources
        self.primary_committed = bool(completed_sources)
        super().__init__(f"End-user Neo4j merge failed for source {source_id}")


class EndUserMergeOutboxError(OutboxEnqueueError):
    """Outbox failed after one source merge committed in Neo4j."""

    def __init__(
        self,
        cause: OutboxEnqueueError,
        *,
        source_id: str,
        affected_nodes: tuple[EndUserMergeNodeIdentity, ...],
        completed_sources: tuple[str, ...],
    ) -> None:
        super().__init__(list(cause.event_ids), cause.reason)
        self.source_id = source_id
        self.affected_nodes = affected_nodes
        self.completed_sources = completed_sources


def _dedup_list(items: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for item in items:
        if isinstance(item, (dict, list)):
            key: Any = json.dumps(item, sort_keys=True, ensure_ascii=False)
        else:
            key = item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _merge_separated_string(target: str, source: str, sep: str = "；") -> str:
    target_parts = [part.strip() for part in target.split(sep) if part.strip()]
    source_parts = [part.strip() for part in source.split(sep) if part.strip()]
    existing = set(target_parts)
    return sep.join(target_parts + [part for part in source_parts if part not in existing])


def _merge_user_properties(
    source: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    list_fields = (
        "aliases",
        "anchors",
        "beliefs_or_stances",
        "core_facts",
        "events",
        "goals",
        "interests",
        "relations",
        "traits",
    )
    merged = {
        field: _dedup_list(
            list(target.get(field) or []) + list(source.get(field) or [])
        )
        for field in list_fields
    }
    merged["description_timeline"] = _merge_separated_string(
        str(target.get("description_timeline") or ""),
        str(source.get("description_timeline") or ""),
    )
    merged["event_timeline"] = _merge_separated_string(
        str(target.get("event_timeline") or ""),
        str(source.get("event_timeline") or ""),
    )

    source_description = str(source.get("description") or "").strip()
    target_description = str(target.get("description") or "").strip()
    merged["description"] = (
        f"{target_description}；{source_description}"
        if target_description and source_description
        else target_description or source_description
    )

    source_summary = str(source.get("description_summary") or "").strip()
    target_summary = str(target.get("description_summary") or "").strip()
    merged["description_summary"] = (
        f"{target_summary}\n{source_summary}"
        if target_summary and source_summary
        else target_summary or source_summary
    )
    return merged


def _parse_snapshot(
    rows: list[dict[str, Any]],
    *,
    owner: str,
) -> list[_SnapshotNode]:
    nodes: list[_SnapshotNode] = []
    element_ids: set[str] = set()
    identities: set[tuple[MemoryNodeType, str]] = set()
    for row in rows:
        element_id = str(row.get("element_id") or "")
        node_id = str(row.get("node_id") or "")
        labels = row.get("memory_labels") or []
        if not element_id or not node_id or len(labels) != 1:
            raise ValueError(
                f"{owner} contains a node without exactly one supported "
                "memory label and a business id"
            )
        try:
            label = MemoryNodeType(labels[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{owner} contains an unsupported memory label") from exc
        identity = (label, node_id)
        if element_id in element_ids:
            raise RuntimeError(f"{owner} snapshot returned duplicate element IDs")
        if identity in identities:
            raise RuntimeError(f"{owner} snapshot returned duplicate node identities")
        element_ids.add(element_id)
        identities.add(identity)
        nodes.append(
            _SnapshotNode(
                element_id=element_id,
                node_id=node_id,
                label=label,
                properties=dict(row.get("properties") or {}),
            )
        )
    return nodes


def _parse_mutation_identity(
    row: dict[str, Any],
    operation: OutboxOperation,
) -> EndUserMergeNodeIdentity:
    element_id = str(row.get("element_id") or "")
    node_id = str(row.get("node_id") or "")
    if not element_id or not node_id:
        raise RuntimeError("End-user merge mutation returned a node without identity")
    try:
        label = MemoryNodeType(row.get("label"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "End-user merge mutation returned an unsupported label"
        ) from exc
    return EndUserMergeNodeIdentity(
        element_id=element_id,
        node_id=node_id,
        label=label,
        operation=operation,
    )


async def _query(tx, cypher: str, **parameters: Any) -> list[dict[str, Any]]:
    result = await tx.run(cypher, **parameters)
    return await result.data()


def _single_count(rows: list[dict[str, Any]], key: str) -> int:
    return int(rows[0].get(key, 0) or 0) if rows else 0


async def _merge_one_source_transaction(
    tx,
    *,
    source_id: str,
    target_id: str,
    supported_labels: list[str],
) -> _SourceMergeResult:
    source_nodes = _parse_snapshot(
        await _query(
            tx,
            END_USER_MERGE_SOURCE_SNAPSHOT,
            source_id=source_id,
            supported_labels=supported_labels,
        ),
        owner="source end user",
    )
    target_users = _parse_snapshot(
        await _query(
            tx,
            END_USER_MERGE_TARGET_USER_SNAPSHOT,
            target_id=target_id,
            supported_labels=supported_labels,
        ),
        owner="target user entity",
    )
    if len(target_users) > 1:
        raise ValueError("target end user contains multiple User entities")
    if target_users and target_users[0].label != MemoryNodeType.EXTRACTED_ENTITY:
        raise ValueError("target User entity has an invalid memory label")

    source_users = [
        node
        for node in source_nodes
        if node.label == MemoryNodeType.EXTRACTED_ENTITY
        and node.properties.get("name") == "用户"
    ]
    if len(source_users) > 1:
        raise ValueError("source end user contains multiple User entities")

    source_user = source_users[0] if source_users else None
    target_user = target_users[0] if target_users else None
    if source_user and target_user and (
        source_user.label,
        source_user.node_id,
    ) == (target_user.label, target_user.node_id):
        raise ValueError("source and target User entities share one business identity")

    target_updated: EndUserMergeNodeIdentity | None = None
    source_deleted: EndUserMergeNodeIdentity | None = None
    redirected_edges = 0
    if source_user is not None and target_user is not None:
        update_rows = await _query(
            tx,
            END_USER_MERGE_UPDATE_TARGET_USER,
            element_id=target_user.element_id,
            target_id=target_id,
            merged_properties=_merge_user_properties(
                source_user.properties,
                target_user.properties,
            ),
        )
        if len(update_rows) != 1:
            raise RuntimeError("target User update did not affect exactly one node")
        target_updated = _parse_mutation_identity(
            update_rows[0], OutboxOperation.UPSERT
        )
        if target_updated.element_id != target_user.element_id:
            raise RuntimeError("target User identity changed during merge")

        incoming_rows = await _query(
            tx,
            END_USER_MERGE_REDIRECT_INCOMING,
            source_element_id=source_user.element_id,
            target_element_id=target_user.element_id,
            target_id=target_id,
        )
        outgoing_rows = await _query(
            tx,
            END_USER_MERGE_REDIRECT_OUTGOING,
            source_element_id=source_user.element_id,
            target_element_id=target_user.element_id,
            target_id=target_id,
        )
        redirected_edges = _single_count(incoming_rows, "redirected") + _single_count(
            outgoing_rows, "redirected"
        )

        delete_rows = await _query(
            tx,
            END_USER_MERGE_DELETE_SOURCE_USER,
            element_id=source_user.element_id,
            source_id=source_id,
        )
        if len(delete_rows) != 1:
            raise RuntimeError("source User delete did not affect exactly one node")
        source_deleted = _parse_mutation_identity(
            delete_rows[0], OutboxOperation.DELETE
        )
        if source_deleted.element_id != source_user.element_id:
            raise RuntimeError("source User identity changed during merge")

    moved_rows = await _query(
        tx,
        END_USER_MERGE_REASSIGN_NODES_WITH_IDENTITIES,
        source_id=source_id,
        target_id=target_id,
        supported_labels=supported_labels,
    )
    moved = [
        _parse_mutation_identity(row, OutboxOperation.UPSERT)
        for row in moved_rows
    ]
    expected_moved = {
        node.element_id: node
        for node in source_nodes
        if source_deleted is None or node.element_id != source_deleted.element_id
    }
    if {node.element_id for node in moved} != set(expected_moved):
        raise RuntimeError("reassigned node identities do not match source snapshot")
    for node in moved:
        expected = expected_moved[node.element_id]
        if (node.label, node.node_id) != (expected.label, expected.node_id):
            raise RuntimeError("reassigned node business identity changed during merge")

    relationship_rows = await _query(
        tx,
        END_USER_MERGE_REASSIGN_RELATIONSHIPS,
        source_id=source_id,
        target_id=target_id,
    )
    reassigned_edges = redirected_edges + _single_count(
        relationship_rows, "updated_edges"
    )

    affected = list(moved)
    if target_updated is not None:
        affected.append(target_updated)
    if source_deleted is not None:
        affected.append(source_deleted)
    unique_identities = {(node.label, node.node_id) for node in affected}
    if len(unique_identities) != len(affected):
        raise RuntimeError("End-user merge returned duplicate affected identities")

    return _SourceMergeResult(
        affected_nodes=tuple(affected),
        reassigned_nodes=len(moved),
        reassigned_edges=reassigned_edges,
    )


async def merge_end_user_memory_nodes(
    source_ids: list[str],
    target_id: str,
    *,
    client: Neo4jClient | None = None,
    outbox_repository: OutboxRepository | None = None,
) -> EndUserMergeStats:
    """Merge each source graph into target and publish exact node events."""
    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("target_id must not be blank")
    if any(not isinstance(source_id, str) or not source_id.strip() for source_id in source_ids):
        raise ValueError("source_ids must not contain blank values")
    ordered_sources = sorted(set(source_ids))
    if target_id in ordered_sources:
        raise ValueError("target_id must not be included in source_ids")
    if not ordered_sources:
        return EndUserMergeStats()

    owns_client = client is None
    if client is None:
        client = await Neo4jClient.create()

    completed_sources: list[str] = []
    reassigned_nodes = 0
    reassigned_edges = 0
    outbox_events = 0
    supported_labels = [label.value for label in MemoryNodeType]
    try:
        for source_id in ordered_sources:
            try:
                result = await client.execute_write_transaction(
                    lambda tx, source_id=source_id: _merge_one_source_transaction(
                        tx,
                        source_id=source_id,
                        target_id=target_id,
                        supported_labels=supported_labels,
                    )
                )
            except Exception as exc:
                raise EndUserMergePrimaryError(
                    source_id=source_id,
                    completed_sources=tuple(completed_sources),
                ) from exc
            events = [
                OutboxEventInput(
                    label=node.label,
                    node_id=node.node_id,
                    operation=node.operation,
                )
                for node in result.affected_nodes
            ]
            try:
                await enqueue_events(events, repository=outbox_repository)
            except OutboxEnqueueError as exc:
                raise EndUserMergeOutboxError(
                    exc,
                    source_id=source_id,
                    affected_nodes=result.affected_nodes,
                    completed_sources=tuple(completed_sources),
                ) from None

            completed_sources.append(source_id)
            reassigned_nodes += result.reassigned_nodes
            reassigned_edges += result.reassigned_edges
            outbox_events += len(events)

        return EndUserMergeStats(
            sources_merged=len(completed_sources),
            reassigned_nodes=reassigned_nodes,
            reassigned_edges=reassigned_edges,
            outbox_events=outbox_events,
        )
    finally:
        if owns_client:
            try:
                await client.close()
            except Exception:
                logger.exception(
                    "Failed to close Neo4j client after end-user merge"
                )
