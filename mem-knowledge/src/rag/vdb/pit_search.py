"""Async search-after iteration for interface-side Elasticsearch exports."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any


async def iter_async_search_after_hits(
    client: Any,
    *,
    index: str,
    query: Mapping[str, Any],
    sort: Sequence[str | Mapping[str, Any]],
    batch_size: int = 1000,
    source_includes: Sequence[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    if not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be between 1 and 10000")
    cursor: list[Any] | None = None
    while True:
        options: dict[str, Any] = {
            "index": index,
            "query": dict(query),
            "sort": list(sort),
            "size": batch_size,
            "allow_partial_search_results": False,
        }
        if source_includes is not None:
            options["source_includes"] = list(source_includes)
        if cursor is not None:
            options["search_after"] = cursor
        response = await client.search(**options)
        if response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Elasticsearch search_after response failed")
        hits = list(response.get("hits", {}).get("hits", []))
        if not hits:
            return
        for hit in hits:
            yield hit
        next_cursor = hits[-1].get("sort")
        if not next_cursor or list(next_cursor) == cursor:
            raise RuntimeError("Elasticsearch search_after cursor did not advance")
        cursor = list(next_cursor)
