"""Elasticsearch aggregation queries over API-owned memory indices."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Integral
from typing import Any

from src.infrastructure.elasticsearch import ElasticsearchManager

#: ES 别名由写侧 ``get_index_name()`` 生成（``{name}_current``）。本服务不 import
#: api，故在此保持同名常量；契约测试见 §11.5，调整时两侧需同步。
_INDEX_MEMORY_SUMMARY = "memory_summary_current"
_INDEX_EXTRACTED_ENTITY = "extracted_entity_current"
_INDEX_STATEMENT = "statement_current"


def _search_header(index: str) -> dict[str, Any]:
    return {"index": index, "allow_partial_search_results": False}


def _search_body(
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


def _validate_response(response: Mapping[str, Any], operation: str) -> None:
    """ES 的部分失败不能被当成 0：``allow_partial_search_results=False`` 只是请求侧
    约束，响应仍需显式检查 error / timed_out / shard failures（api 同款判定）。"""

    if response.get("error") is not None:
        raise RuntimeError(f"Elasticsearch {operation} failed: {response['error']!r}")
    if response.get("timed_out"):
        raise RuntimeError(f"Elasticsearch {operation} timed out")
    shards = response.get("_shards") or {}
    if isinstance(shards, Mapping) and int(shards.get("failed", 0) or 0):
        raise RuntimeError(
            f"Elasticsearch {operation} shard failures: {shards.get('failures', [])!r}"
        )


def _parse_buckets(
    response: Mapping[str, Any],
    operation: str,
    sub_aggregation: str | None = None,
) -> dict[str, tuple[int, int]]:
    _validate_response(response, operation)
    aggregations = response.get("aggregations")
    by_end_user = aggregations.get("by_end_user") if isinstance(aggregations, Mapping) else None
    buckets = by_end_user.get("buckets") if isinstance(by_end_user, Mapping) else None
    if not isinstance(buckets, list):
        raise RuntimeError(
            f"Elasticsearch {operation} returned invalid aggregation buckets"
        )

    parsed: dict[str, tuple[int, int]] = {}
    for bucket in buckets:
        if not isinstance(bucket, Mapping):
            raise RuntimeError(f"Elasticsearch {operation} returned an invalid bucket")
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
                filtered.get("doc_count") if isinstance(filtered, Mapping) else None
            )
            if not isinstance(filtered_count, Integral) or isinstance(
                filtered_count, bool
            ):
                raise RuntimeError(
                    f"Elasticsearch {operation} returned an invalid sub-aggregation"
                )
            statistic_count = int(filtered_count)
        parsed[str(key)] = (int(doc_count), statistic_count)
    return parsed


class MemorySearchRepository:
    """Aggregate episodic, explicit and emotional counts from Elasticsearch."""

    def __init__(self, elasticsearch: ElasticsearchManager) -> None:
        self.elasticsearch = elasticsearch

    async def workspace_memory_counts(
        self,
        end_user_ids: Sequence[str],
    ) -> tuple[dict[str, dict[str, int]], set[str]]:
        """返回每个用户的 ES 计数，以及「在任一索引中存在有效数据」的用户集合。

        一次 ``msearch`` 并发三个聚合（情景摘要 / 显性实体 / 情绪语句），避免三次
        往返；调用方据此对缺失用户回退到 Neo4j。
        """

        if not end_user_ids:
            return {}, set()
        client = await self.elasticsearch.client()
        searches = [
            _search_header(_INDEX_MEMORY_SUMMARY),
            _search_body(end_user_ids),
            _search_header(_INDEX_EXTRACTED_ENTITY),
            _search_body(
                end_user_ids,
                ("explicit", {"filter": {"term": {"is_explicit_memory": True}}}),
            ),
            _search_header(_INDEX_STATEMENT),
            _search_body(
                end_user_ids,
                ("emotional", {"filter": {"exists": {"field": "emotion_type"}}}),
            ),
        ]
        result = await client.msearch(searches=searches, max_concurrent_searches=3)
        responses = result.get("responses")
        if not isinstance(responses, list) or len(responses) != 3:
            raise RuntimeError(
                "Elasticsearch workspace statistics returned invalid responses"
            )
        if not all(isinstance(response, Mapping) for response in responses):
            raise RuntimeError(
                "Elasticsearch workspace statistics returned an invalid response"
            )
        episodic = _parse_buckets(responses[0], "workspace episodic")
        explicit = _parse_buckets(responses[1], "workspace explicit", "explicit")
        emotional = _parse_buckets(responses[2], "workspace emotional", "emotional")

        requested_ids = set(end_user_ids)
        present_ids = (set(episodic) | set(explicit) | set(emotional)) & requested_ids
        statistics = {
            end_user_id: {
                "episodic_count": episodic.get(end_user_id, (0, 0))[1],
                "explicit_entity_count": explicit.get(end_user_id, (0, 0))[1],
                "emotional_count": emotional.get(end_user_id, (0, 0))[1],
            }
            for end_user_id in end_user_ids
        }
        return statistics, present_ids
