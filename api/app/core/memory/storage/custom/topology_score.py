"""Custom storage query for Neo4j GDS topology-score computation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.repositories.neo4j.cypher_queries import CLEAR_GRAPH, G_SCORE

logger = logging.getLogger(__name__)

GDS_GRAPH_BUILD_WITH_AFFECTED_NODES = """
MATCH (source)
WHERE source.end_user_id = $end_user_id
  AND source.delete_at IS NULL
  AND source.id IS NOT NULL
  AND any(label IN labels(source) WHERE label IN [
      'Statement', 'MemorySummary', 'Chunk', 'ExtractedEntity', 'Perceptual'
  ])
  AND (source.name IS NULL OR source.name <> '用户')
OPTIONAL MATCH (source)-[r]-(target)
WHERE target.end_user_id = $end_user_id
  AND target.delete_at IS NULL
  AND target.id IS NOT NULL
  AND source <> target
  AND any(label IN labels(target) WHERE label IN [
      'Statement', 'MemorySummary', 'Chunk', 'ExtractedEntity', 'Perceptual'
  ])
  AND (target.name IS NULL OR target.name <> '用户')
WITH source, target, count(r) AS parallel_count
WITH
    gds.graph.project(
        $end_user_id,
        source,
        target,
        {relationshipProperties: {weight: toFloat(parallel_count)}}
    ) AS graph,
    collect(DISTINCT {
        label: head([
            label IN [
                'Statement', 'MemorySummary', 'Chunk',
                'ExtractedEntity', 'Perceptual'
            ]
            WHERE label IN labels(source)
        ]),
        node_id: toString(source.id)
    }) AS affectedNodes
RETURN graph.graphName AS graphName,
       graph.nodeCount AS nodeCount,
       graph.relationshipCount AS relationshipCount,
       graph.projectMillis AS projectMillis,
       affectedNodes
"""


@dataclass(frozen=True, slots=True)
class _AffectedNode:
    label: MemoryNodeType
    node_id: str


async def compute_topology_score(
    end_user_id: str,
    *,
    neo4j_client: Neo4jClient | None = None,
    outbox_repository: OutboxRepository | None = None,
) -> Dict[str, Any]:
    """Compute topology scores and enqueue the exact projected node snapshot.

    The affected identities are collected by the same Cypher aggregation that
    builds the GDS graph, so they cannot drift due to a later database query.
    Neo4j still commits the GDS result before PostgreSQL commits the Outbox
    events. An enqueue failure therefore has ``primary_committed=True``.
    """
    owns_client = neo4j_client is None
    client = neo4j_client or await Neo4jClient.create()
    summary: dict[str, Any] | None = None
    affected_nodes: list[_AffectedNode] = []

    try:
        projection_rows = await client.execute_query(
            GDS_GRAPH_BUILD_WITH_AFFECTED_NODES,
            end_user_id=end_user_id,
        )
        affected_rows = (
            projection_rows[0].get("affectedNodes", [])
            if projection_rows
            else []
        )
        affected_nodes = [
            _AffectedNode(
                label=MemoryNodeType(row["label"]),
                node_id=str(row["node_id"]),
            )
            for row in affected_rows
        ]

        try:
            score_rows = await client.execute_query(
                G_SCORE,
                end_user_id=end_user_id,
            )
        except Exception as exc:
            # 活跃用户可能尚无记忆节点，投影得到空图，eigenvector 对空图会报错。
            # 这种情况记 skipped（非失败），避免每次 scan 都产生一条 FAILURE 任务。
            logger.warning(
                "GDS eigenvector.write 失败（可能为空图） user=%s: %s",
                end_user_id,
                exc,
            )
            return {
                "status": "skipped",
                "reason": "eigenvector_failed",
                "error": str(exc),
            }

        summary = score_rows[0] if score_rows else {}
        unique_nodes = {(node.label, node.node_id) for node in affected_nodes}
        if len(unique_nodes) != len(affected_nodes):
            raise RuntimeError("GDS projection returned duplicate node identities")

        written = int(summary.get("nodePropertiesWritten", 0) or 0)
        if written != len(affected_nodes):
            raise RuntimeError(
                "GDS write count does not match projected nodes: "
                f"written={written} affected={len(affected_nodes)}"
            )
    finally:
        # 无论成败都释放内存图，避免残留投影图占用 Neo4j 内存。
        try:
            await client.execute_query(
                CLEAR_GRAPH,
                end_user_id=end_user_id,
            )
        except Exception as exc:
            logger.warning(
                "GDS graph.drop 失败 user=%s: %s",
                end_user_id,
                exc,
            )
        if owns_client:
            await client.close()

    events = [
        OutboxEventInput(
            label=node.label,
            node_id=node.node_id,
            operation=OutboxOperation.UPSERT,
        )
        for node in affected_nodes
    ]
    event_ids = await enqueue_events(
        events,
        repository=outbox_repository,
    )

    assert summary is not None
    return {
        "status": "success",
        "node_properties_written": summary.get("nodePropertiesWritten", 0),
        "did_converge": summary.get("didConverge"),
        "ran_iterations": summary.get("ranIterations"),
        "outbox_events": len(event_ids),
    }
