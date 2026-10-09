"""SceneSummary/SceneCommunity storage boundary with precise outbox events.

The caller owns and injects :class:`Neo4jClient`.  Model calls and batch
planning happen outside Neo4j transactions; only the final community upserts
and SceneSummary assignments share one transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.neo4j.client import Neo4jClient


SCENE_SUMMARY_CREATE_IF_ABSENT_WITH_IDENTITY = """
MERGE (s:SceneSummary {id: $summary.id, end_user_id: $summary.end_user_id})
ON CREATE SET s = $summary
RETURN elementId(s) AS element_id,
       toString(s.id) AS node_id
"""

SCENE_SUMMARY_IDENTITY = """
MATCH (s:SceneSummary {id: $scene_summary_id, end_user_id: $end_user_id})
RETURN elementId(s) AS element_id,
       toString(s.id) AS node_id
"""

SCENE_SUMMARY_SOURCE_IDS = """
MATCH (s:SceneSummary {id: $scene_summary_id, end_user_id: $end_user_id})
RETURN s.source_message_ids AS source_message_ids
"""

SCENE_SUMMARY_INACTIVE_COUNT = """
MATCH (s:SceneSummary {end_user_id: $end_user_id, community_status: 'INACTIVE'})
RETURN count(s) AS inactive_count
"""

SCENE_SUMMARY_INACTIVE_BATCH = """
MATCH (s:SceneSummary {end_user_id: $end_user_id, community_status: 'INACTIVE'})
WITH s
ORDER BY s.created_at ASC, s.id ASC
LIMIT $batch_size
RETURN properties(s) AS scene_summary
"""

SCENE_COMMUNITY_CANDIDATES_ALL = """
MATCH (c:SceneCommunity {end_user_id: $end_user_id, category_l1: $category_l1})
RETURN properties(c) AS community, 0.0 AS similarity
ORDER BY c.updated_at DESC, c.id ASC
"""

SCENE_COMMUNITY_CANDIDATES_LIMITED = """
MATCH (c:SceneCommunity {end_user_id: $end_user_id, category_l1: $category_l1})
WITH c, coalesce(vector.similarity.cosine(c.summary_embedding, $summary_embedding), -1.0) AS similarity
RETURN properties(c) AS community, similarity
ORDER BY similarity DESC, c.updated_at DESC, c.id ASC
LIMIT $candidate_limit
"""

SCENE_COMMUNITY_MEMBERS = """
MATCH (s:SceneSummary {end_user_id: $end_user_id, community_status: 'ACTIVE'})
WHERE s.scene_community_id IN $community_ids
RETURN s.scene_community_id AS scene_community_id,
       properties(s) AS scene_summary
ORDER BY s.created_at ASC, s.id ASC
"""

SCENE_COMMUNITY_UPSERT_BATCH = """
UNWIND $communities AS row
MERGE (c:SceneCommunity {id: row.id, end_user_id: $end_user_id})
ON CREATE SET c.created_at = row.created_at
WITH c, row, c.created_at AS original_created_at
SET c += row,
    c.created_at = original_created_at
RETURN elementId(c) AS element_id,
       toString(c.id) AS node_id
ORDER BY node_id
"""

SCENE_SUMMARY_ASSIGN_BATCH = """
UNWIND $assignments AS row
MATCH (s:SceneSummary {
    id: row.scene_summary_id,
    end_user_id: $end_user_id,
    community_status: 'INACTIVE'
})
MATCH (c:SceneCommunity {id: row.scene_community_id, end_user_id: $end_user_id})
SET s.scene_community_id = c.id,
    s.community_category_l1 = row.category_l1,
    s.community_status = 'ACTIVE',
    s.updated_at = row.updated_at
RETURN elementId(s) AS element_id,
       toString(s.id) AS node_id
ORDER BY node_id
"""


@dataclass(frozen=True, slots=True)
class SceneMutationIdentity:
    label: MemoryNodeType
    element_id: str
    node_id: str


class SceneStorageOutboxError(OutboxEnqueueError):
    """Neo4j committed, but projection events could not be enqueued."""

    def __init__(
        self,
        cause: OutboxEnqueueError,
        affected_nodes: Sequence[SceneMutationIdentity],
    ) -> None:
        super().__init__(list(cause.event_ids), cause.reason)
        self.affected_nodes = tuple(affected_nodes)


def _nonblank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


def _parse_identities(
    rows: list[dict[str, Any]],
    *,
    label: MemoryNodeType,
    expected_ids: set[str],
) -> list[SceneMutationIdentity]:
    identities: list[SceneMutationIdentity] = []
    node_ids: set[str] = set()
    element_ids: set[str] = set()
    for row in rows:
        node_id = str(row.get("node_id") or "")
        element_id = str(row.get("element_id") or "")
        if not node_id or not element_id:
            raise RuntimeError("Scene mutation returned a blank node identity")
        if node_id in node_ids or element_id in element_ids:
            raise RuntimeError("Scene mutation returned duplicate node identities")
        node_ids.add(node_id)
        element_ids.add(element_id)
        identities.append(SceneMutationIdentity(label, element_id, node_id))
    if node_ids != expected_ids:
        raise RuntimeError(
            f"Scene mutation identity mismatch: expected={sorted(expected_ids)!r}, "
            f"actual={sorted(node_ids)!r}"
        )
    return identities


async def _tx_rows(tx: Any, query: str, **parameters: Any) -> list[dict[str, Any]]:
    statement = await tx.run(query, **parameters)
    return [dict(record) for record in await statement.data()]


class SceneStorage:
    """Custom graph writer/read boundary for the incremental scene pipeline."""

    def __init__(
        self,
        client: Neo4jClient,
        *,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self.client = client
        self.outbox_repository = outbox_repository

    async def _publish(self, identities: Sequence[SceneMutationIdentity]) -> None:
        events = [
            OutboxEventInput(
                label=identity.label,
                node_id=identity.node_id,
                operation=OutboxOperation.UPSERT,
            )
            for identity in sorted(
                identities,
                key=lambda item: (item.label.value, item.node_id),
            )
        ]
        if not events:
            return
        try:
            await enqueue_events(events, repository=self.outbox_repository)
        except OutboxEnqueueError as exc:
            raise SceneStorageOutboxError(exc, identities) from None

    async def create_scene_summary_if_absent(
        self,
        summary: dict[str, Any],
    ) -> SceneMutationIdentity:
        scene_summary_id = _nonblank(summary.get("id"), "summary.id")
        _nonblank(summary.get("end_user_id"), "summary.end_user_id")
        identities = await self.client.execute_validated_write_query(
            SCENE_SUMMARY_CREATE_IF_ABSENT_WITH_IDENTITY,
            lambda rows: _parse_identities(
                rows,
                label=MemoryNodeType.SCENE_SUMMARY,
                expected_ids={scene_summary_id},
            ),
            summary=summary,
        )
        await self._publish(identities)
        return identities[0]

    async def republish_scene_summary(
        self,
        scene_summary_id: str,
        end_user_id: str,
    ) -> None:
        scene_summary_id = _nonblank(scene_summary_id, "scene_summary_id")
        end_user_id = _nonblank(end_user_id, "end_user_id")
        rows = await self.client.execute_query(
            SCENE_SUMMARY_IDENTITY,
            scene_summary_id=scene_summary_id,
            end_user_id=end_user_id,
        )
        identities = _parse_identities(
            rows,
            label=MemoryNodeType.SCENE_SUMMARY,
            expected_ids={scene_summary_id},
        )
        await self._publish(identities)

    async def get_scene_summary_source_ids(
        self,
        scene_summary_id: str,
        end_user_id: str,
    ) -> list[str] | None:
        rows = await self.client.execute_query(
            SCENE_SUMMARY_SOURCE_IDS,
            scene_summary_id=_nonblank(scene_summary_id, "scene_summary_id"),
            end_user_id=_nonblank(end_user_id, "end_user_id"),
        )
        if not rows:
            return None
        return list(rows[0].get("source_message_ids") or [])

    async def count_inactive(self, end_user_id: str) -> int:
        rows = await self.client.execute_query(
            SCENE_SUMMARY_INACTIVE_COUNT,
            end_user_id=_nonblank(end_user_id, "end_user_id"),
        )
        return int(rows[0].get("inactive_count") or 0) if rows else 0

    async def load_inactive_batch(
        self,
        end_user_id: str,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        rows = await self.client.execute_query(
            SCENE_SUMMARY_INACTIVE_BATCH,
            end_user_id=_nonblank(end_user_id, "end_user_id"),
            batch_size=batch_size,
        )
        return [dict(row["scene_summary"]) for row in rows]

    async def load_candidate_communities(
        self,
        *,
        end_user_id: str,
        category_l1: str,
        summary_embedding: list[float],
        candidate_limit: int,
        compare_all: bool,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(candidate_limit, int)
            or isinstance(candidate_limit, bool)
            or candidate_limit <= 0
        ):
            raise ValueError("candidate_limit must be a positive integer")
        query = (
            SCENE_COMMUNITY_CANDIDATES_ALL
            if compare_all
            else SCENE_COMMUNITY_CANDIDATES_LIMITED
        )
        rows = await self.client.execute_query(
            query,
            end_user_id=_nonblank(end_user_id, "end_user_id"),
            category_l1=_nonblank(category_l1, "category_l1"),
            summary_embedding=summary_embedding,
            candidate_limit=candidate_limit,
        )
        return [
            {**dict(row["community"]), "_similarity": float(row.get("similarity") or 0.0)}
            for row in rows
        ]

    async def load_community_members(
        self,
        end_user_id: str,
        community_ids: Sequence[str],
    ) -> dict[str, list[dict[str, Any]]]:
        unique_ids = sorted({_nonblank(value, "community_id") for value in community_ids})
        grouped = {community_id: [] for community_id in unique_ids}
        if not unique_ids:
            return grouped
        rows = await self.client.execute_query(
            SCENE_COMMUNITY_MEMBERS,
            end_user_id=_nonblank(end_user_id, "end_user_id"),
            community_ids=unique_ids,
        )
        for row in rows:
            grouped[str(row["scene_community_id"])].append(dict(row["scene_summary"]))
        return grouped

    async def commit_batch(
        self,
        *,
        end_user_id: str,
        communities: Sequence[dict[str, Any]],
        assignments: Sequence[dict[str, Any]],
    ) -> list[SceneMutationIdentity]:
        end_user_id = _nonblank(end_user_id, "end_user_id")
        community_ids = {_nonblank(row.get("id"), "community.id") for row in communities}
        community_categories = {
            str(row["id"]): _nonblank(row.get("category_l1"), "community.category_l1")
            for row in communities
        }
        scene_summary_ids = {
            _nonblank(row.get("scene_summary_id"), "assignment.scene_summary_id")
            for row in assignments
        }
        if len(community_ids) != len(communities):
            raise ValueError("communities must contain unique ids")
        if len(scene_summary_ids) != len(assignments):
            raise ValueError("assignments must contain unique SceneSummary ids")
        if not communities or not assignments:
            raise ValueError("a complete scene community batch cannot be empty")
        if any(row.get("end_user_id") != end_user_id for row in communities):
            raise ValueError("every community must belong to end_user_id")
        if any(row.get("scene_community_id") not in community_ids for row in assignments):
            raise ValueError("every assignment must target a community in this transaction")
        if any(
            row.get("category_l1")
            != community_categories.get(str(row.get("scene_community_id")))
            for row in assignments
        ):
            raise ValueError("assignment category must match its target community")

        async def _commit(tx: Any) -> list[SceneMutationIdentity]:
            community_rows = await _tx_rows(
                tx,
                SCENE_COMMUNITY_UPSERT_BATCH,
                end_user_id=end_user_id,
                communities=list(communities),
            )
            community_identities = _parse_identities(
                community_rows,
                label=MemoryNodeType.SCENE_COMMUNITY,
                expected_ids=community_ids,
            )
            scene_rows = await _tx_rows(
                tx,
                SCENE_SUMMARY_ASSIGN_BATCH,
                end_user_id=end_user_id,
                assignments=list(assignments),
            )
            scene_identities = _parse_identities(
                scene_rows,
                label=MemoryNodeType.SCENE_SUMMARY,
                expected_ids=scene_summary_ids,
            )
            return [*community_identities, *scene_identities]

        identities = await self.client.execute_write_transaction(_commit)
        await self._publish(identities)
        return identities
