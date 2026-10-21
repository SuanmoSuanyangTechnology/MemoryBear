"""Workspace statistics and short-term memo queries for Elasticsearch and Neo4j."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from numbers import Integral
from typing import Any, Protocol

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.elasticsearch.index import get_index_name
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.utils.datetime_utils import to_timestamp_ms

_SHORT_TERM_MEMO_BATCH_SIZE = 500


class MemoQueryExecutor(Protocol):
    async def execute_query(
        self, cypher: str, **parameters: Any,
    ) -> list[dict[str, Any]]: ...


# Keep type validation before temporal comparisons: legacy strings are not dates.
_SHORT_TERM_ELIGIBLE_STATEMENTS = """
MATCH (s:Statement)
WHERE s.end_user_id IN $end_user_ids
  AND s.delete_at IS NULL
  AND s.speaker = 'user'
  AND s.is_permanent = false
  AND s.id IS NOT NULL
  AND CASE WHEN valueType(s.statement) = 'STRING NOT NULL'
           THEN trim(s.statement) <> '' ELSE false END
WITH s,
     CASE WHEN s.valid_at IS NULL THEN true
          WHEN valueType(s.valid_at) = 'STRING NOT NULL' THEN trim(s.valid_at) = ''
          ELSE false END AS start_missing,
     CASE WHEN s.invalid_at IS NULL THEN true
          WHEN valueType(s.invalid_at) = 'STRING NOT NULL' THEN trim(s.invalid_at) = ''
          ELSE false END AS end_missing,
     CASE WHEN valueType(s.valid_at) = 'ZONED DATETIME NOT NULL'
          THEN datetime({datetime: s.valid_at, timezone: 'UTC'}) END AS original_start,
     CASE WHEN valueType(s.invalid_at) = 'ZONED DATETIME NOT NULL'
          THEN datetime({datetime: s.invalid_at, timezone: 'UTC'}) END AS end_at,
     CASE WHEN valueType(s.dialog_at) = 'ZONED DATETIME NOT NULL'
          THEN datetime({datetime: s.dialog_at, timezone: 'UTC'})
          WHEN valueType(s.created_at) = 'ZONED DATETIME NOT NULL'
          THEN datetime({datetime: s.created_at, timezone: 'UTC'})
          END AS mentioned_at
WHERE (start_missing OR original_start IS NOT NULL)
  AND (end_missing OR end_at IS NOT NULL)
WITH s, end_at, mentioned_at, start_missing AND end_missing AS is_time_unspecified,
     CASE WHEN start_missing AND end_missing THEN mentioned_at
          ELSE original_start END AS start_at
WHERE CASE
    WHEN start_at IS NOT NULL AND end_at IS NOT NULL THEN
        start_at <= end_at AND (
            (start_at >= $window_start AND start_at <= $window_end)
            OR (end_at >= $window_start AND end_at <= $window_end)
            OR (start_at <= $now AND $now <= end_at
                AND end_at <= start_at + duration({hours: 168}))
        )
    WHEN start_at IS NOT NULL THEN
        start_at >= $window_start AND start_at <= $window_end
    WHEN end_at IS NOT NULL THEN
        end_at >= $window_start AND end_at <= $window_end
    ELSE false
END
"""

_SHORT_TERM_MEMOS_QUERY = _SHORT_TERM_ELIGIBLE_STATEMENTS + """
RETURN s.id AS id, s.statement AS statement,
       start_at, end_at, mentioned_at, is_time_unspecified
"""

_SHORT_TERM_COUNT_QUERY = _SHORT_TERM_ELIGIBLE_STATEMENTS + """
RETURN count(DISTINCT s.id) AS total
"""


# Date doc values are already normalized by ES. Source text is needed because
# statement is a text field without doc values, and blank statements are excluded.
_SHORT_TERM_ELIGIBLE_SCRIPT = """
def text = params._source.statement;
if (!(text instanceof String) || text.trim().length() == 0) {
    emit(false);
    return;
}
def start = null;
def end = null;
if (doc.containsKey('valid_at') && doc['valid_at'].size() > 0) {
    start = doc['valid_at'].value.toInstant().toEpochMilli();
}
if (doc.containsKey('invalid_at') && doc['invalid_at'].size() > 0) {
    end = doc['invalid_at'].value.toInstant().toEpochMilli();
}
if (start == null && end == null) {
    if (doc.containsKey('dialog_at') && doc['dialog_at'].size() > 0) {
        start = doc['dialog_at'].value.toInstant().toEpochMilli();
    } else if (doc.containsKey('created_at') && doc['created_at'].size() > 0) {
        start = doc['created_at'].value.toInstant().toEpochMilli();
    }
}
long now = params.now;
long proximity = params.proximity;
if (start != null && end != null) {
    boolean nearStart = start >= now - proximity && start <= now + proximity;
    boolean nearEnd = end >= now - proximity && end <= now + proximity;
    boolean withinShortEvent = start <= now && now <= end
        && end - start <= params.max_span;
    emit(start <= end && (nearStart || nearEnd || withinShortEvent));
} else if (start != null) {
    emit(start >= now - proximity && start <= now + proximity);
} else if (end != null) {
    emit(end >= now - proximity && end <= now + proximity);
} else {
    emit(false);
}
"""


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


def _elasticsearch_short_term_body(
    end_user_ids: Sequence[str], now: datetime,
) -> dict[str, Any]:
    eligible_filter = {
        "bool": {
            "filter": [
                {"term": {"speaker": "user"}},
                {"term": {"is_permanent": False}},
                {"exists": {"field": "id"}},
                {"term": {"memo_eligible": True}},
            ],
        },
    }
    body = _elasticsearch_search_body(end_user_ids, ("short_term", {"filter": eligible_filter}))
    body["runtime_mappings"] = {
        "memo_eligible": {
            "type": "boolean",
            "script": {
                "source": _SHORT_TERM_ELIGIBLE_SCRIPT,
                "params": {
                    "now": to_timestamp_ms(now),
                    "proximity": 72 * 60 * 60 * 1000,
                    "max_span": 168 * 60 * 60 * 1000,
                },
            },
        },
    }
    return body


async def get_elasticsearch_short_term_counts(
    client: ElasticClient, end_user_ids: Sequence[str], now: datetime,
) -> tuple[dict[str, int], set[str]]:
    """Return eligible counts and users with active Statement documents.

    Presence is measured before eligibility filtering, so a valid zero does
    not cause Neo4j fallback. Statement IDs are ES document IDs on the writer.
    """
    if not end_user_ids:
        return {}, set()
    body = _elasticsearch_short_term_body(end_user_ids, now)
    if client.client is None:
        raise RuntimeError("Elasticsearch client is not connected")
    response = await client.client.search(
        index=get_index_name(MemoryNodeType.STATEMENT), body=body, allow_partial_search_results=False,
    )
    buckets = _parse_elasticsearch_buckets(response, "short-term memos", "short_term")
    present = set(buckets) & set(end_user_ids)
    return {user_id: buckets[user_id][1] for user_id in present}, present



def _elasticsearch_memo_projection(hit: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize ES date doc values to the same UTC projection as Neo4j."""
    source = hit["_source"]
    fields = hit.get("fields", {})

    def date_value(name: str) -> datetime | None:
        values = fields.get(name, [])
        if not values:
            return None
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(values[0]))

    start = date_value("valid_at")
    end = date_value("invalid_at")
    mentioned = date_value("dialog_at") or date_value("created_at")
    unspecified = start is None and end is None
    return {
        "id": source["id"],
        "statement": source["statement"],
        "start_at": mentioned if unspecified else start,
        "end_at": end,
        "mentioned_at": mentioned,
        "is_time_unspecified": unspecified,
    }


async def get_elasticsearch_short_term_memos(
    client: ElasticClient, end_user_id: str, now: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """Read all eligible Statements in a PIT; distinguish no data from zero eligible.

    The presence aggregation runs before post_filter, so an existing user with
    no eligible memos does not fall back. PIT batches are internal reads; display
    sorting and page/pagesize pagination remain in the service.
    """
    if client.client is None:
        raise RuntimeError("Elasticsearch client is not connected")
    body = _elasticsearch_short_term_body([end_user_id], now)
    eligible_filter = body.pop("aggs")["by_end_user"]["aggs"]["short_term"]["filter"]
    body.update({
        "size": _SHORT_TERM_MEMO_BATCH_SIZE,
        "sort": ["_shard_doc"],
        "track_total_hits": False,
        "_source": ["id", "statement"],
        "fields": [{"field": name, "format": "epoch_millis"}
                   for name in ("valid_at", "invalid_at", "dialog_at", "created_at")],
        "post_filter": eligible_filter,
        "aggs": {"statement_presence": {"filter": {"match_all": {}}}},
    })
    opened = await client.client.open_point_in_time(
        index=get_index_name(MemoryNodeType.STATEMENT), keep_alive="1m",
        allow_partial_search_results=False,
    )
    pit_id = opened.get("id")
    if not isinstance(pit_id, str) or not pit_id:
        raise RuntimeError("Elasticsearch returned no PIT ID for short-term memos")
    rows: list[dict[str, Any]] = []
    present = False
    first_page = True
    try:
        _validate_elasticsearch_response(opened, "short-term PIT")
        while True:
            body["pit"] = {"id": pit_id, "keep_alive": "1m"}
            response = await client.client.search(body=body, allow_partial_search_results=False)
            pit_id = response.get("pit_id") or pit_id
            _validate_elasticsearch_response(response, "short-term memo list")
            if first_page:
                presence = response.get("aggregations", {}).get("statement_presence", {})
                count = presence.get("doc_count")
                if not isinstance(count, Integral) or isinstance(count, bool):
                    raise RuntimeError("Elasticsearch returned invalid Statement presence")
                present = count > 0
                first_page = False
                body.pop("aggs")
            hits = response.get("hits", {}).get("hits")
            if not isinstance(hits, list):
                raise RuntimeError("Elasticsearch returned invalid short-term hits")
            rows.extend(_elasticsearch_memo_projection(hit) for hit in hits)
            if len(hits) < _SHORT_TERM_MEMO_BATCH_SIZE:
                break
            position = hits[-1].get("sort")
            if not isinstance(position, (list, tuple)) or not position:
                raise RuntimeError("Elasticsearch returned no short-term search position")
            body["search_after"] = list(position)
    finally:
        await client.client.close_point_in_time(id=pit_id)
    return rows, present


def _short_term_parameters(end_user_ids: Sequence[str], now: datetime) -> dict[str, Any]:
    return {
        "end_user_ids": list(end_user_ids),
        "now": now,
        "window_start": now - timedelta(hours=72),
        "window_end": now + timedelta(hours=72),
    }


async def get_neo4j_short_term_memos(
    client: MemoQueryExecutor, end_user_id: str, now: datetime,
) -> list[dict[str, Any]]:
    """Read all eligible memo projections for in-memory counts and pagination."""
    return await client.execute_query(
        _SHORT_TERM_MEMOS_QUERY, **_short_term_parameters([end_user_id], now),
    )


async def get_neo4j_short_term_count(
    client: MemoQueryExecutor, end_user_ids: Sequence[str], now: datetime,
) -> int:
    """Count distinct eligible Statements for the ES fallback user batch."""
    if not end_user_ids:
        return 0
    rows = await client.execute_query(
        _SHORT_TERM_COUNT_QUERY, **_short_term_parameters(end_user_ids, now),
    )
    return int(rows[0]["total"]) if rows else 0


class WorkspaceStatisticsStorage:
    """Workspace memory statistics over caller-provided Elasticsearch and Neo4j clients."""

    def __init__(
        self,
        elastic_client: ElasticClient,
        neo4j_client: Neo4jClient,
    ) -> None:
        self._elastic_client = elastic_client
        self._neo4j_client = neo4j_client

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

        async with asyncio.TaskGroup() as task_group:
            elastic_task = task_group.create_task(
                get_elasticsearch_workspace_statistics(
                    self._elastic_client,
                    end_user_ids,
                )
            )
            implicit_task = task_group.create_task(
                get_neo4j_implicit_memory_count(
                    self._neo4j_client,
                    end_user_ids,
                    minimum_summary_count,
                )
            )
        elastic_statistics, present_ids = elastic_task.result()
        implicit_count = implicit_task.result()

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
                self._neo4j_client,
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
