"""Reassign and count RAG messages when end users are merged.

Messages live in the ``RagMemory`` Elasticsearch index. A merge rewrites the
``end_user_id`` field of every non-deleted source message, then counts the
target's messages for the ``memory_count`` cache.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from elasticsearch import AsyncElasticsearch

from src.storage.enums import MemoryNodeType
from src.storage.provider.elasticsearch.index import get_index_name

logger = logging.getLogger(__name__)

INDEX_NAME = get_index_name(MemoryNodeType.RAG_MEMORY)
_ACTIVE_CLAUSE: dict[str, Any] = {
    "bool": {"must_not": [{"exists": {"field": "delete_at"}}]}
}


async def reassign_rag_messages(
    client: AsyncElasticsearch,
    source_end_user_ids: Iterable[object],
    target_end_user_id: object,
) -> int:
    """Move non-deleted source messages to the target; idempotent on retry."""

    source_ids = [str(value) for value in source_end_user_ids if value]
    if not source_ids:
        return 0

    result = await client.update_by_query(
        index=INDEX_NAME,
        query={
            "bool": {
                "filter": [{"terms": {"end_user_id": source_ids}}],
                "must_not": [{"exists": {"field": "delete_at"}}],
            }
        },
        script={
            "lang": "painless",
            "source": "ctx._source.end_user_id = params.target",
            "params": {"target": str(target_end_user_id)},
        },
        conflicts="proceed",
        refresh=True,
        slices="auto",
    )
    total = int(result.get("total", 0) or 0)
    updated = int(result.get("updated", 0) or 0)
    failures = result.get("failures") or []
    if failures or result.get("timed_out"):
        raise RuntimeError(
            "update_by_query incomplete: "
            f"sources={source_ids} target={target_end_user_id} "
            f"matched={total} updated={updated} failures={failures!r}"
        )
    if updated < total:
        # Version conflicts are only visible as updated < total; treating it as
        # success would leave orphaned messages under a soft-deleted source.
        raise RuntimeError(
            "update_by_query incomplete: "
            f"sources={source_ids} target={target_end_user_id} "
            f"matched={total} updated={updated} "
            f"version_conflicts={result.get('version_conflicts', 0)}"
        )
    return updated


async def count_rag_messages(
    client: AsyncElasticsearch, end_user_ids: Iterable[object]
) -> int:
    ids = [str(value) for value in end_user_ids]
    if not ids:
        return 0
    result = await client.count(
        index=INDEX_NAME,
        query={"bool": {"filter": [_ACTIVE_CLAUSE, {"terms": {"end_user_id": ids}}]}},
    )
    return int(result.get("count", 0))


def build_rag_merge(manager: Any) -> Callable[[list[str], str], Awaitable[int]]:
    """Adapt RAG reassignment to the merge service's callback."""

    async def _merge(source_ids: list[str], target_id: str) -> int:
        client = await manager.client()
        return await reassign_rag_messages(client, source_ids, target_id)

    return _merge


def build_rag_memory_count(
    manager: Any,
) -> Callable[[str], Awaitable[int]]:
    async def _count(end_user_id: str) -> int:
        client = await manager.client()
        return await count_rag_messages(client, [end_user_id])

    return _count


__all__ = [
    "INDEX_NAME",
    "build_rag_memory_count",
    "build_rag_merge",
    "count_rag_messages",
    "reassign_rag_messages",
]