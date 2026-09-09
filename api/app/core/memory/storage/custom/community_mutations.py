from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from neo4j.exceptions import Neo4jError

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

COMMUNITY_ASSIGN_CHUNK_SIZE = 500
_DEADLOCK_MAX_RETRY = 3

COMMUNITY_UPSERT_WITH_IDENTITY = """
MERGE (c:Community {community_id: $community_id})
ON CREATE SET c.id = $community_id
SET c.end_user_id = $end_user_id,
    c.member_count = $member_count,
    c.updated_at = datetime()
RETURN elementId(c) AS element_id,
       toString(c.community_id) AS community_id
"""

COMMUNITY_ASSIGN_ENTITIES_WITH_IDENTITIES = """
UNWIND $assignments AS row
MATCH (e:ExtractedEntity {id: row.entity_id, end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
MATCH (c:Community {
    community_id: row.community_id,
    end_user_id: $end_user_id
})
OPTIONAL MATCH (e)-[old_r:BELONGS_TO_COMMUNITY]->(:Community)
DELETE old_r
WITH DISTINCT e, c
MERGE (e)-[:BELONGS_TO_COMMUNITY]->(c)
WITH DISTINCT c
SET c.updated_at = datetime()
RETURN elementId(c) AS element_id,
       toString(c.community_id) AS community_id
ORDER BY community_id
"""

COMMUNITY_REFRESH_MEMBER_COUNTS_WITH_IDENTITIES = """
UNWIND $community_ids AS community_id
MATCH (c:Community {
    community_id: community_id,
    end_user_id: $end_user_id
})
OPTIONAL MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
      -[:BELONGS_TO_COMMUNITY]->(c)
WHERE e.delete_at IS NULL
WITH c, count(e) AS member_count
SET c.member_count = member_count
RETURN elementId(c) AS element_id,
       toString(c.community_id) AS community_id
ORDER BY community_id
"""

COMMUNITY_UPDATE_METADATA_WITH_IDENTITIES = """
UNWIND $communities AS row
MATCH (c:Community {
    community_id: row.community_id,
    end_user_id: row.end_user_id
})
SET c.id                = coalesce(c.id, row.community_id),
    c.name              = row.name,
    c.summary           = row.summary,
    c.core_entities     = row.core_entities,
    c.summary_embedding = row.summary_embedding,
    c.updated_at        = datetime()
RETURN elementId(c) AS element_id,
       toString(c.community_id) AS community_id
ORDER BY community_id
"""

COMMUNITY_DELETE_EMPTY_WITH_IDENTITIES = """
MATCH (c:Community {end_user_id: $end_user_id})
WHERE NOT EXISTS {
    MATCH (:ExtractedEntity {end_user_id: $end_user_id})
          -[:BELONGS_TO_COMMUNITY]->(c)
}
WITH c,
     elementId(c) AS element_id,
     toString(c.community_id) AS community_id
ORDER BY community_id
DETACH DELETE c
RETURN element_id, community_id
ORDER BY community_id
"""

COMMUNITY_REFRESH_ALL_WITH_IDENTITIES = """
MATCH (c:Community {end_user_id: $end_user_id})
OPTIONAL MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
      -[:BELONGS_TO_COMMUNITY]->(c)
WHERE e.delete_at IS NULL
WITH c, count(e) AS member_count
SET c.member_count = member_count
RETURN elementId(c) AS element_id,
       toString(c.community_id) AS community_id
ORDER BY community_id
"""

COMMUNITY_REFRESH_SCOPED_WITH_IDENTITIES = """
MATCH (c:Community {end_user_id: $end_user_id})
WHERE c.community_id IN $community_ids
OPTIONAL MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
      -[:BELONGS_TO_COMMUNITY]->(c)
WHERE e.delete_at IS NULL
WITH c, count(e) AS member_count
SET c.member_count = member_count
RETURN elementId(c) AS element_id,
       toString(c.community_id) AS community_id
ORDER BY community_id
"""


@dataclass(frozen=True, slots=True)
class CommunityNodeIdentity:
    element_id: str
    community_id: str


@dataclass(frozen=True, slots=True)
class CommunityReconcileStats:
    deleted: int
    refreshed: int


class CommunityMutationOutboxError(OutboxEnqueueError):
    """Outbox failed after Community nodes were committed in Neo4j."""

    def __init__(
        self,
        cause: OutboxEnqueueError,
        affected_nodes: Sequence[CommunityNodeIdentity],
        operations: Sequence[OutboxOperation],
    ) -> None:
        super().__init__(list(cause.event_ids), cause.reason)
        self.affected_nodes = tuple(affected_nodes)
        self.operations = tuple(operations)


def _nonblank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


def _unique_nonblank(values: Iterable[str], field: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _nonblank(value, field)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _parse_identities(
    rows: list[dict[str, Any]],
    *,
    allowed_ids: set[str] | None = None,
    exact_ids: set[str] | None = None,
) -> list[CommunityNodeIdentity]:
    identities: list[CommunityNodeIdentity] = []
    element_ids: set[str] = set()
    community_ids: set[str] = set()
    for row in rows:
        element_id = str(row.get("element_id") or "")
        community_id = str(row.get("community_id") or "")
        if not element_id or not community_id:
            raise RuntimeError("Community mutation returned a blank node identity")
        if element_id in element_ids:
            raise RuntimeError("Community mutation returned duplicate element IDs")
        if community_id in community_ids:
            raise RuntimeError("Community mutation returned duplicate business IDs")
        if allowed_ids is not None and community_id not in allowed_ids:
            raise RuntimeError("Community mutation returned an unexpected business ID")
        element_ids.add(element_id)
        community_ids.add(community_id)
        identities.append(
            CommunityNodeIdentity(
                element_id=element_id,
                community_id=community_id,
            )
        )

    if exact_ids is not None and community_ids != exact_ids:
        raise RuntimeError("Community mutation did not return the expected identities")
    return identities


async def _tx_rows(tx: Any, query: str, **parameters: Any) -> list[dict[str, Any]]:
    statement = await tx.run(query, **parameters)
    return [dict(record) for record in await statement.data()]


class CommunityMutationWriter:
    """Storage custom writer for Community nodes and membership side effects.

    Every node mutation returns and validates ``community_id`` plus Neo4j
    ``elementId`` inside the managed transaction. Outbox events are enqueued
    only after Neo4j commit. Membership relationships do not emit events, but
    assignment updates ``Community.updated_at`` and therefore emits UPSERT for
    each actually affected Community node.
    """

    def __init__(
        self,
        client: Neo4jClient,
        *,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self.client = client
        self.outbox_repository = outbox_repository

    async def _publish(
        self,
        identities: Sequence[CommunityNodeIdentity],
        operation: OutboxOperation,
    ) -> None:
        if not identities:
            return
        events = [
            OutboxEventInput(
                label=MemoryNodeType.COMMUNITY,
                node_id=identity.community_id,
                operation=operation,
            )
            for identity in identities
        ]
        try:
            await enqueue_events(events, repository=self.outbox_repository)
        except OutboxEnqueueError as exc:
            raise CommunityMutationOutboxError(
                exc,
                identities,
                [operation] * len(identities),
            ) from None

    async def _publish_reconcile(
        self,
        deleted: Sequence[CommunityNodeIdentity],
        refreshed: Sequence[CommunityNodeIdentity],
    ) -> None:
        events = [
            *[
                OutboxEventInput(
                    label=MemoryNodeType.COMMUNITY,
                    node_id=node.community_id,
                    operation=OutboxOperation.DELETE,
                )
                for node in deleted
            ],
            *[
                OutboxEventInput(
                    label=MemoryNodeType.COMMUNITY,
                    node_id=node.community_id,
                    operation=OutboxOperation.UPSERT,
                )
                for node in refreshed
            ],
        ]
        if not events:
            return
        identities = [*deleted, *refreshed]
        operations = [
            *([OutboxOperation.DELETE] * len(deleted)),
            *([OutboxOperation.UPSERT] * len(refreshed)),
        ]
        try:
            await enqueue_events(events, repository=self.outbox_repository)
        except OutboxEnqueueError as exc:
            raise CommunityMutationOutboxError(
                exc,
                identities,
                operations,
            ) from None

    async def upsert_community(
        self,
        community_id: str,
        end_user_id: str,
        member_count: int = 0,
    ) -> CommunityNodeIdentity:
        community_id = _nonblank(community_id, "community_id")
        end_user_id = _nonblank(end_user_id, "end_user_id")
        if not isinstance(member_count, int) or isinstance(member_count, bool) or member_count < 0:
            raise ValueError("member_count must be a non-negative integer")

        identities = await self.client.execute_validated_write_query(
            COMMUNITY_UPSERT_WITH_IDENTITY,
            lambda rows: _parse_identities(rows, exact_ids={community_id}),
            community_id=community_id,
            end_user_id=end_user_id,
            member_count=member_count,
        )
        await self._publish(identities, OutboxOperation.UPSERT)
        return identities[0]

    async def assign_entities_to_communities(
        self,
        assignments: Sequence[dict[str, str]],
        end_user_id: str,
        *,
        chunk_size: int = COMMUNITY_ASSIGN_CHUNK_SIZE,
    ) -> list[CommunityNodeIdentity]:
        end_user_id = _nonblank(end_user_id, "end_user_id")
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        normalized: list[dict[str, str]] = []
        target_by_entity: dict[str, str] = {}
        for assignment in assignments:
            entity_id = _nonblank(assignment.get("entity_id"), "entity_id")
            community_id = _nonblank(
                assignment.get("community_id"), "community_id"
            )
            previous_target = target_by_entity.get(entity_id)
            if previous_target is not None:
                if previous_target != community_id:
                    raise ValueError(
                        "one entity cannot be assigned to multiple communities"
                    )
                continue
            target_by_entity[entity_id] = community_id
            normalized.append(
                {
                    "entity_id": entity_id,
                    "community_id": community_id,
                }
            )
        if not normalized:
            return []

        return await self._assign_normalized(
            normalized,
            end_user_id,
            chunk_size=chunk_size,
        )

    async def _assign_normalized(
        self,
        normalized: list[dict[str, str]],
        end_user_id: str,
        *,
        chunk_size: int,
    ) -> list[CommunityNodeIdentity]:
        affected: list[CommunityNodeIdentity] = []
        for start in range(0, len(normalized), chunk_size):
            chunk = normalized[start:start + chunk_size]
            allowed_ids = {item["community_id"] for item in chunk}
            for attempt in range(1, _DEADLOCK_MAX_RETRY + 1):
                try:
                    identities = await self.client.execute_validated_write_query(
                        COMMUNITY_ASSIGN_ENTITIES_WITH_IDENTITIES,
                        lambda rows, allowed=allowed_ids: _parse_identities(
                            rows,
                            allowed_ids=allowed,
                        ),
                        assignments=chunk,
                        end_user_id=end_user_id,
                    )
                    break
                except Neo4jError as exc:
                    if (
                        exc.code == "Neo.TransientError.Transaction.DeadlockDetected"
                        and attempt < _DEADLOCK_MAX_RETRY
                    ):
                        await asyncio.sleep(0.2 * attempt)
                        continue
                    raise
            await self._publish(identities, OutboxOperation.UPSERT)
            affected.extend(identities)
        return affected

    async def assign_entity_to_community(
        self,
        entity_id: str,
        community_id: str,
        end_user_id: str,
    ) -> bool:
        affected = await self.assign_entities_to_communities(
            [{"entity_id": entity_id, "community_id": community_id}],
            end_user_id,
        )
        return bool(affected)

    async def refresh_member_counts(
        self,
        community_ids: Iterable[str],
        end_user_id: str,
    ) -> list[CommunityNodeIdentity]:
        end_user_id = _nonblank(end_user_id, "end_user_id")
        unique_ids = _unique_nonblank(community_ids, "community_id")
        if not unique_ids:
            return []
        allowed_ids = set(unique_ids)
        identities = await self.client.execute_validated_write_query(
            COMMUNITY_REFRESH_MEMBER_COUNTS_WITH_IDENTITIES,
            lambda rows: _parse_identities(rows, allowed_ids=allowed_ids),
            community_ids=unique_ids,
            end_user_id=end_user_id,
        )
        await self._publish(identities, OutboxOperation.UPSERT)
        return identities

    async def refresh_member_count(
        self,
        community_id: str,
        end_user_id: str,
    ) -> int:
        identities = await self.refresh_member_counts([community_id], end_user_id)
        return len(identities)

    async def update_community_metadata(
        self,
        communities: Sequence[dict[str, Any]],
    ) -> list[CommunityNodeIdentity]:
        if not communities:
            return []
        normalized: list[dict[str, Any]] = []
        allowed_ids: set[str] = set()
        for community in communities:
            community_id = _nonblank(community.get("community_id"), "community_id")
            end_user_id = _nonblank(community.get("end_user_id"), "end_user_id")
            if community_id in allowed_ids:
                raise ValueError("communities contains duplicate community_id values")
            allowed_ids.add(community_id)
            normalized.append(
                {
                    "community_id": community_id,
                    "end_user_id": end_user_id,
                    "name": community.get("name"),
                    "summary": community.get("summary"),
                    "core_entities": community.get("core_entities"),
                    "summary_embedding": community.get("summary_embedding"),
                }
            )

        identities = await self.client.execute_validated_write_query(
            COMMUNITY_UPDATE_METADATA_WITH_IDENTITIES,
            lambda rows: _parse_identities(rows, allowed_ids=allowed_ids),
            communities=normalized,
        )
        await self._publish(identities, OutboxOperation.UPSERT)
        return identities

    async def reconcile_after_clustering(
        self,
        end_user_id: str,
        refresh_community_ids: Sequence[str] | None = None,
    ) -> CommunityReconcileStats:
        end_user_id = _nonblank(end_user_id, "end_user_id")
        scoped_ids = (
            None
            if refresh_community_ids is None
            else _unique_nonblank(refresh_community_ids, "community_id")
        )

        async def _operation(tx: Any) -> tuple[
            list[CommunityNodeIdentity],
            list[CommunityNodeIdentity],
        ]:
            deleted = _parse_identities(
                await _tx_rows(
                    tx,
                    COMMUNITY_DELETE_EMPTY_WITH_IDENTITIES,
                    end_user_id=end_user_id,
                )
            )
            if scoped_ids is None:
                refresh_query = COMMUNITY_REFRESH_ALL_WITH_IDENTITIES
                refresh_parameters: dict[str, Any] = {"end_user_id": end_user_id}
            elif scoped_ids:
                refresh_query = COMMUNITY_REFRESH_SCOPED_WITH_IDENTITIES
                refresh_parameters = {
                    "end_user_id": end_user_id,
                    "community_ids": scoped_ids,
                }
            else:
                return deleted, []

            refreshed = _parse_identities(
                await _tx_rows(tx, refresh_query, **refresh_parameters),
                allowed_ids=set(scoped_ids) if scoped_ids is not None else None,
            )
            if {node.community_id for node in deleted} & {
                node.community_id for node in refreshed
            }:
                raise RuntimeError(
                    "Community reconcile returned a deleted node as refreshed"
                )
            return deleted, refreshed

        deleted, refreshed = await self.client.execute_write_transaction(_operation)
        await self._publish_reconcile(deleted, refreshed)
        return CommunityReconcileStats(
            deleted=len(deleted),
            refreshed=len(refreshed),
        )
