"""Workspace memory statistics queries for Elasticsearch and Neo4j."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from numbers import Integral
from typing import Any

from app.core.memory.storage.enums import BackendType, MemoryNodeType
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.elasticsearch.index import get_index_name
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

_IMPLICIT_MEMORY_QUERY = """
UNWIND $end_user_ids AS end_user_id
OPTIONAL MATCH (summary:MemorySummary {end_user_id: end_user_id})
      -[:DERIVED_FROM_STATEMENT]->(:Statement)
WHERE summary.delete_at IS NULL
WITH end_user_id, count(DISTINCT summary) AS summary_count
RETURN sum(
    CASE WHEN summary_count >= $minimum_summary_count
         THEN summary_count ELSE 0 END
) AS implicit_count
"""

_FALLBACK_STATISTICS_QUERY = """
UNWIND $end_user_ids AS end_user_id
CALL (end_user_id) {
    OPTIONAL MATCH (summary:MemorySummary {end_user_id: end_user_id})
    WHERE summary.delete_at IS NULL
    RETURN count(DISTINCT summary) AS episodic_count
}
CALL (end_user_id) {
    OPTIONAL MATCH (entity:ExtractedEntity {end_user_id: end_user_id})
    WHERE entity.delete_at IS NULL
      AND entity.is_explicit_memory = true
    RETURN count(DISTINCT entity) AS explicit_entity_count
}
CALL (end_user_id) {
    OPTIONAL MATCH (statement:Statement {end_user_id: end_user_id})
    WHERE statement.delete_at IS NULL
      AND statement.emotion_type IS NOT NULL
    RETURN count(DISTINCT statement) AS emotional_count
}
RETURN sum(episodic_count) AS episodic_count,
       sum(explicit_entity_count) AS explicit_entity_count,
       sum(emotional_count) AS emotional_count
"""


def _elasticsearch_search_header(label: MemoryNodeType) -> dict[str, Any]:
    return {
        "index": get_index_name(label),
        "allow_partial_search_results": False,
    }


def _elasticsearch_search_body(
    end_user_ids: Sequence[str],
    sub_aggregation: tuple[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_end_user: dict[str, Any] = {
        "terms": {
            "field": "end_user_id",
            "size": len(end_user_ids),
        }
    }
    if sub_aggregation is not None:
        name, aggregation = sub_aggregation
        by_end_user["aggs"] = {name: aggregation}
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [{"terms": {"end_user_id": list(end_user_ids)}}],
                "must_not": [{"exists": {"field": "delete_at"}}],
            }
        },
        "aggs": {"by_end_user": by_end_user},
    }


def _validate_elasticsearch_response(
    response: Mapping[str, Any],
    operation: str,
) -> None:
    if response.get("error") is not None:
        raise RuntimeError(
            f"Elasticsearch {operation} failed: {response['error']!r}"
        )
    if response.get("timed_out"):
        raise RuntimeError(f"Elasticsearch {operation} timed out")
    shards = response.get("_shards") or {}
    if isinstance(shards, Mapping) and int(shards.get("failed", 0) or 0):
        raise RuntimeError(
            f"Elasticsearch {operation} shard failures: "
            f"{shards.get('failures', [])!r}"
        )


def _parse_elasticsearch_buckets(
    response: Mapping[str, Any],
    operation: str,
    sub_aggregation: str | None = None,
) -> dict[str, tuple[int, int]]:
    _validate_elasticsearch_response(response, operation)
    aggregations = response.get("aggregations")
    by_end_user = (
        aggregations.get("by_end_user")
        if isinstance(aggregations, Mapping)
        else None
    )
    buckets = (
        by_end_user.get("buckets")
        if isinstance(by_end_user, Mapping)
        else None
    )
    if not isinstance(buckets, list):
        raise RuntimeError(
            f"Elasticsearch {operation} returned invalid aggregation buckets"
        )

    parsed: dict[str, tuple[int, int]] = {}
    for bucket in buckets:
        if not isinstance(bucket, Mapping):
            raise RuntimeError(
                f"Elasticsearch {operation} returned an invalid bucket"
            )
        key = bucket.get("key_as_string", bucket.get("key"))
        doc_count = bucket.get("doc_count")
        if (
            key is None
            or not isinstance(doc_count, Integral)
            or isinstance(doc_count, bool)
        ):
            raise RuntimeError(
                f"Elasticsearch {operation} returned an invalid bucket value"
            )

        statistic_count = int(doc_count)
        if sub_aggregation is not None:
            filtered = bucket.get(sub_aggregation)
            filtered_count = (
                filtered.get("doc_count")
                if isinstance(filtered, Mapping)
                else None
            )
            if (
                not isinstance(filtered_count, Integral)
                or isinstance(filtered_count, bool)
            ):
                raise RuntimeError(
                    f"Elasticsearch {operation} returned an invalid sub-aggregation"
                )
            statistic_count = int(filtered_count)
        parsed[str(key)] = (int(doc_count), statistic_count)
    return parsed


async def get_elasticsearch_workspace_statistics(
    client: ElasticClient,
    end_user_ids: Sequence[str],
) -> tuple[dict[str, dict[str, int]], set[str]]:
    """返回每个用户的三类计数及 ES 中存在活跃节点的用户集合。"""
    if not end_user_ids:
        return {}, set()
    if client.client is None:
        raise RuntimeError("Elasticsearch client is not connected")

    searches = [
        _elasticsearch_search_header(MemoryNodeType.MEMORY_SUMMARY),
        _elasticsearch_search_body(end_user_ids),
        _elasticsearch_search_header(MemoryNodeType.EXTRACTED_ENTITY),
        _elasticsearch_search_body(
            end_user_ids,
            ("explicit", {"filter": {"term": {"is_explicit_memory": True}}}),
        ),
        _elasticsearch_search_header(MemoryNodeType.STATEMENT),
        _elasticsearch_search_body(
            end_user_ids,
            ("emotional", {"filter": {"exists": {"field": "emotion_type"}}}),
        ),
    ]
    result = await client.client.msearch(
        searches=searches,
        max_concurrent_searches=3,
    )
    responses = result.get("responses")
    if not isinstance(responses, list) or len(responses) != 3:
        raise RuntimeError(
            "Elasticsearch workspace statistics returned invalid responses"
        )
    if not all(isinstance(response, Mapping) for response in responses):
        raise RuntimeError(
            "Elasticsearch workspace statistics returned an invalid response"
        )
    episodic = _parse_elasticsearch_buckets(
        responses[0],
        "workspace episodic",
    )
    explicit = _parse_elasticsearch_buckets(
        responses[1],
        "workspace explicit",
        "explicit",
    )
    emotional = _parse_elasticsearch_buckets(
        responses[2],
        "workspace emotional",
        "emotional",
    )

    requested_ids = set(end_user_ids)
    present_ids = (
        set(episodic) | set(explicit) | set(emotional)
    ) & requested_ids
    statistics = {
        end_user_id: {
            "episodic_count": episodic.get(end_user_id, (0, 0))[1],
            "explicit_entity_count": explicit.get(end_user_id, (0, 0))[1],
            "emotional_count": emotional.get(end_user_id, (0, 0))[1],
        }
        for end_user_id in end_user_ids
    }
    return statistics, present_ids


async def get_neo4j_implicit_memory_count(
    client: Neo4jClient,
    end_user_ids: Sequence[str],
    minimum_summary_count: int,
) -> int:
    if not end_user_ids:
        return 0
    rows = await client.execute_query(
        _IMPLICIT_MEMORY_QUERY,
        end_user_ids=list(end_user_ids),
        minimum_summary_count=minimum_summary_count,
    )
    return int(rows[0].get("implicit_count") or 0) if rows else 0


async def get_neo4j_fallback_statistics(
    client: Neo4jClient,
    end_user_ids: Sequence[str],
) -> dict[str, int]:
    if not end_user_ids:
        return {
            "episodic_count": 0,
            "explicit_entity_count": 0,
            "emotional_count": 0,
        }
    rows = await client.execute_query(
        _FALLBACK_STATISTICS_QUERY,
        end_user_ids=list(end_user_ids),
    )
    row = rows[0] if rows else {}
    return {
        "episodic_count": int(row.get("episodic_count") or 0),
        "explicit_entity_count": int(row.get("explicit_entity_count") or 0),
        "emotional_count": int(row.get("emotional_count") or 0),
    }


class WorkspaceStatisticsStorage:
    """Workspace memory statistics over shared Elasticsearch and Neo4j clients."""

    def __init__(self, backend_factory: BackendFactory) -> None:
        self._backend_factory = backend_factory

    async def get_statistics(
        self,
        end_user_ids: Sequence[str],
        minimum_summary_count: int,
    ) -> dict[str, int]:
        """Aggregate episodic, explicit, emotional and implicit memories."""
        if not end_user_ids:
            return {
                "episodic_count": 0,
                "explicit_count": 0,
                "emotional_count": 0,
                "implicit_count": 0,
            }

        elastic_client = self._backend_factory.get_client(
            BackendType.ELASTIC
        )
        if not isinstance(elastic_client, ElasticClient):
            raise TypeError("ELASTIC backend must be an ElasticClient")
        neo4j_client = self._backend_factory.get_client(BackendType.NEO4J)
        if not isinstance(neo4j_client, Neo4jClient):
            raise TypeError("NEO4J backend must be a Neo4jClient")

        elastic_task = get_elasticsearch_workspace_statistics(
            elastic_client,
            end_user_ids,
        )
        implicit_task = get_neo4j_implicit_memory_count(
            neo4j_client,
            end_user_ids,
            minimum_summary_count,
        )
        (elastic_statistics, present_ids), implicit_count = (
            await asyncio.gather(elastic_task, implicit_task)
        )

        episodic_count = sum(
            item["episodic_count"] for item in elastic_statistics.values()
        )
        explicit_entity_count = sum(
            item["explicit_entity_count"]
            for item in elastic_statistics.values()
        )
        emotional_count = sum(
            item["emotional_count"] for item in elastic_statistics.values()
        )

        missing_ids = [
            end_user_id
            for end_user_id in end_user_ids
            if end_user_id not in present_ids
        ]
        if missing_ids:
            fallback = await get_neo4j_fallback_statistics(
                neo4j_client,
                missing_ids,
            )
            episodic_count += fallback["episodic_count"]
            explicit_entity_count += fallback["explicit_entity_count"]
            emotional_count += fallback["emotional_count"]

        return {
            "episodic_count": episodic_count,
            "explicit_count": episodic_count + explicit_entity_count,
            "emotional_count": emotional_count,
            "implicit_count": implicit_count,
        }
