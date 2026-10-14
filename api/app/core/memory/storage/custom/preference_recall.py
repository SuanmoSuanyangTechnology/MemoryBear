"""Exact Elasticsearch reads for Coding Agent preference recall."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.memory.storage.enums import BackendType, MemoryNodeType
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.elasticsearch.client import PIT_KEEP_ALIVE, SEARCH_BATCH_SIZE
from app.core.memory.storage.provider.elasticsearch.index import get_index_name
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage_services.preference_engine.models import (
    PreferenceRecallTarget,
)


@dataclass(frozen=True)
class PreferenceRecallCandidate:
    preference_text: str
    updated_at: datetime
    target_index: int
    is_global: bool
    item_index: int


@dataclass(frozen=True)
class FullPreferenceCandidate:
    preference_text: str
    updated_at: datetime
    is_global: bool
    document_id: str
    item_index: int


def _search_header() -> dict[str, Any]:
    return {
        "index": get_index_name(MemoryNodeType.PREFERENCE),
        "allow_partial_search_results": False,
    }


def _search_body(
    *,
    end_user_id: str,
    subject: str,
    situation_key: str,
    size: int,
) -> dict[str, Any]:
    """Build an exact keyword-only query; never use full text or vectors."""
    return {
        "size": size,
        "_source": ["preference_text", "updated_at"],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"end_user_id": end_user_id}},
                    {"term": {"domain": "coding"}},
                    {"term": {"subject": subject}},
                    {"term": {"situation_key": situation_key}},
                    {"term": {"status": "active"}},
                ]
            }
        },
        "sort": [
            {"updated_at": {"order": "desc", "unmapped_type": "date"}},
            {"id": {"order": "asc", "unmapped_type": "keyword"}},
        ],
    }


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise RuntimeError("Elasticsearch preference hit has invalid updated_at")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_response(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if response.get("error") is not None:
        raise RuntimeError(f"Elasticsearch preference recall failed: {response['error']!r}")
    if response.get("timed_out"):
        raise RuntimeError("Elasticsearch preference recall timed out")
    shards = response.get("_shards") or {}
    if isinstance(shards, Mapping) and int(shards.get("failed", 0) or 0):
        raise RuntimeError("Elasticsearch preference recall had shard failures")
    hits_container = response.get("hits")
    hits = hits_container.get("hits") if isinstance(hits_container, Mapping) else None
    if not isinstance(hits, list) or not all(isinstance(hit, Mapping) for hit in hits):
        raise RuntimeError("Elasticsearch preference recall returned invalid hits")
    return hits


class PreferenceRecallStorage:
    """Preference bucket read-side backed by the process-wide ES client."""

    def __init__(self, backend_factory: BackendFactory):
        client = backend_factory.get_client(BackendType.ELASTIC)
        if not isinstance(client, ElasticClient):
            raise RuntimeError("Preference recall requires the Elasticsearch backend")
        self._client = client

    async def query_buckets(
        self,
        *,
        end_user_id: str,
        targets: Sequence[PreferenceRecallTarget],
        per_bucket_limit: int,
    ) -> list[PreferenceRecallCandidate]:
        if not targets:
            return []
        raw_client = self._client.client
        if raw_client is None:
            raise RuntimeError("Elasticsearch client is not connected")

        searches: list[dict[str, Any]] = []
        buckets: list[tuple[int, bool]] = []
        for target_index, target in enumerate(targets):
            searches.extend(
                [
                    _search_header(),
                    _search_body(
                        end_user_id=end_user_id,
                        subject=target.subject,
                        situation_key=target.situation_key,
                        size=per_bucket_limit,
                    ),
                ]
            )
            buckets.append((target_index, target.situation_key == "global"))
            if target.situation_key != "global":
                searches.extend(
                    [
                        _search_header(),
                        _search_body(
                            end_user_id=end_user_id,
                            subject=target.subject,
                            situation_key="global",
                            size=per_bucket_limit,
                        ),
                    ]
                )
                buckets.append((target_index, True))

        result = await raw_client.msearch(
            searches=searches,
            max_concurrent_searches=max(1, min(len(buckets), 10)),
        )
        body = getattr(result, "body", result)
        responses = body.get("responses") if isinstance(body, Mapping) else None
        if not isinstance(responses, list) or len(responses) != len(buckets):
            raise RuntimeError("Elasticsearch preference recall response count mismatch")

        candidates: list[PreferenceRecallCandidate] = []
        for response, (target_index, is_global) in zip(responses, buckets, strict=True):
            if not isinstance(response, Mapping):
                raise RuntimeError("Elasticsearch preference recall returned invalid response")
            for hit in _validate_response(response):
                source = hit.get("_source")
                if not isinstance(source, Mapping):
                    raise RuntimeError("Elasticsearch preference hit has invalid _source")
                texts = source.get("preference_text")
                if isinstance(texts, str):
                    texts = [texts]
                if not isinstance(texts, list) or not all(
                    isinstance(text, str) and text.strip() for text in texts
                ):
                    raise RuntimeError("Elasticsearch preference hit has invalid preference_text")
                updated_at = _parse_datetime(source.get("updated_at"))
                candidates.extend(
                    PreferenceRecallCandidate(
                        preference_text=text,
                        updated_at=updated_at,
                        target_index=target_index,
                        is_global=is_global,
                        item_index=item_index,
                    )
                    for item_index, text in enumerate(texts)
                )
        return candidates

    async def query_all(self, *, end_user_id: str) -> tuple[list[FullPreferenceCandidate], int]:
        """Read the complete active Coding preference PIT snapshot from Elasticsearch."""
        raw_client = self._client.client
        if raw_client is None:
            raise RuntimeError("Elasticsearch client is not connected")
        opened = await raw_client.open_point_in_time(
            index=get_index_name(MemoryNodeType.PREFERENCE), keep_alive=PIT_KEEP_ALIVE
        )
        pit = getattr(opened, "body", opened)
        if not isinstance(pit, Mapping) or not isinstance(pit.get("id"), str) or not pit["id"]:
            raise RuntimeError("Elasticsearch preference PIT has no id")
        pit_id = pit["id"]
        candidates: list[FullPreferenceCandidate] = []
        pages = 0
        expected_total: int | None = None
        search_after: list[Any] | None = None
        try:
            while True:
                options: dict[str, Any] = {
                    "pit": {"id": pit_id, "keep_alive": PIT_KEEP_ALIVE},
                    "query": {"bool": {"filter": [
                        {"term": {"end_user_id": end_user_id}},
                        {"term": {"domain": "coding"}},
                        {"term": {"status": "active"}},
                    ]}},
                    "source_includes": ["id", "situation_key", "preference_text", "updated_at"],
                    "size": SEARCH_BATCH_SIZE,
                    "sort": [{"_shard_doc": "asc"}],
                    "track_total_hits": True,
                    "allow_partial_search_results": False,
                }
                if search_after is not None:
                    options["search_after"] = search_after
                result = await raw_client.search(**options)
                response = getattr(result, "body", result)
                if not isinstance(response, Mapping):
                    raise RuntimeError("Elasticsearch preference page is invalid")
                if isinstance(response.get("pit_id"), str) and response["pit_id"]:
                    pit_id = response["pit_id"]
                if response.get("timed_out") is not False:
                    raise RuntimeError("Elasticsearch preference page timeout is unknown")
                shards = response.get("_shards")
                if (not isinstance(shards, Mapping)
                        or not isinstance(shards.get("total"), int)
                        or not isinstance(shards.get("successful"), int)
                        or not isinstance(shards.get("failed"), int)
                        or shards["failed"] != 0
                        or shards["successful"] + shards.get("skipped", 0) != shards["total"]):
                    raise RuntimeError("Elasticsearch preference page has incomplete shards")
                hits = _validate_response(response)
                total = response["hits"].get("total")
                if (not isinstance(total, Mapping) or total.get("relation") != "eq"
                        or not isinstance(total.get("value"), int) or total["value"] < 0):
                    raise RuntimeError("Elasticsearch preference total is invalid")
                if expected_total is None:
                    expected_total = total["value"]
                elif expected_total != total["value"]:
                    raise RuntimeError("Elasticsearch preference PIT total changed")
                if len(hits) > SEARCH_BATCH_SIZE or pages * SEARCH_BATCH_SIZE + len(hits) > expected_total:
                    raise RuntimeError("Elasticsearch preference page count is invalid")
                for hit in hits:
                    source = hit.get("_source")
                    if not isinstance(source, Mapping):
                        raise RuntimeError("Elasticsearch preference source is invalid")
                    document_id = source.get("id")
                    situation_key = source.get("situation_key")
                    texts = source.get("preference_text")
                    if (not isinstance(document_id, str) or not document_id
                            or not isinstance(situation_key, str) or not situation_key
                            or not isinstance(texts, list)
                            or not all(isinstance(item, str) for item in texts)):
                        raise RuntimeError("Elasticsearch preference document is invalid")
                    updated_at = _parse_datetime(source.get("updated_at"))
                    candidates.extend(
                        FullPreferenceCandidate(
                            text, updated_at, situation_key == "global", document_id, index
                        )
                        for index, text in enumerate(texts)
                    )
                pages += 1
                read_count = (pages - 1) * SEARCH_BATCH_SIZE + len(hits)
                if read_count == expected_total:
                    break
                if not hits or len(hits) != SEARCH_BATCH_SIZE:
                    raise RuntimeError("Elasticsearch preference PIT is incomplete")
                sort = hits[-1].get("sort")
                if not isinstance(sort, (list, tuple)) or not sort:
                    raise RuntimeError("Elasticsearch preference search_after is missing")
                search_after = list(sort)
            return candidates, pages
        finally:
            await raw_client.close_point_in_time(id=pit_id)
