from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from elasticsearch import Elasticsearch

logger = logging.getLogger(__name__)

DEFAULT_PIT_KEEP_ALIVE = "2m"
DEFAULT_SEARCH_AFTER_BATCH_SIZE = 1000
MAX_SEARCH_AFTER_BATCH_SIZE = 10000


@dataclass(frozen=True)
class PitSearchPage:
    total: int | None
    hits: list[dict[str, Any]]


def _total_value(response: Mapping[str, Any]) -> int:
    total = response.get("hits", {}).get("total", 0)
    if isinstance(total, Mapping):
        return int(total.get("value", 0))
    return int(total or 0)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _failure_summary(failures: Any) -> str:
    if not isinstance(failures, list) or not failures:
        return ""
    summaries: list[str] = []
    for failure in failures[:3]:
        if isinstance(failure, Mapping):
            reason = failure.get("reason") or failure.get("type") or failure.get("cause")
            if isinstance(reason, Mapping):
                reason = reason.get("reason") or reason.get("type")
            summaries.append(str(reason or "unknown"))
        else:
            summaries.append(str(failure))
    return "; ".join(summaries)


def _raise_on_search_failure(response: Mapping[str, Any]) -> None:
    if response.get("timed_out"):
        raise RuntimeError("Elasticsearch PIT search timed out")

    shards = response.get("_shards") or {}
    failed = _int_value(shards.get("failed") if isinstance(shards, Mapping) else 0)
    failures = []
    if isinstance(shards, Mapping):
        failures = shards.get("failures") or []
    if not failures:
        failures = response.get("failures") or []

    if failed or failures:
        details = _failure_summary(failures)
        message = f"Elasticsearch PIT search failed on {failed} shard(s)"
        if details:
            message = f"{message}: {details}"
        raise RuntimeError(message)


def _with_shard_tiebreaker(
    sort: Sequence[str | Mapping[str, Any]],
) -> list[str | Mapping[str, Any]]:
    result = list(sort)
    has_shard_doc = any(
        value == "_shard_doc"
        or (isinstance(value, Mapping) and "_shard_doc" in value)
        for value in result
    )
    if not has_shard_doc:
        result.append({"_shard_doc": "asc"})
    return result


def iter_pit_search_pages(
    client: Elasticsearch,
    *,
    index: str | Sequence[str],
    query: Mapping[str, Any],
    sort: Sequence[str | Mapping[str, Any]] = (),
    batch_size: int = DEFAULT_SEARCH_AFTER_BATCH_SIZE,
    source: bool | Mapping[str, Any] | None = None,
    source_includes: Sequence[str] | None = None,
    track_total_hits: bool = False,
    keep_alive: str = DEFAULT_PIT_KEEP_ALIVE,
) -> Iterator[PitSearchPage]:
    if not 1 <= batch_size <= MAX_SEARCH_AFTER_BATCH_SIZE:
        raise ValueError("batch_size must be between 1 and 10000")

    pit_id: str | None = None
    cursor: list[Any] | None = None
    first_page = True
    try:
        opened = client.open_point_in_time(index=index, keep_alive=keep_alive)
        pit_id = opened.get("id")
        if not pit_id:
            raise RuntimeError("Elasticsearch did not return a PIT id")

        effective_sort = _with_shard_tiebreaker(sort)
        while True:
            search_kwargs: dict[str, Any] = {
                "pit": {"id": pit_id, "keep_alive": keep_alive},
                "query": dict(query),
                "sort": effective_sort,
                "size": batch_size,
                "track_total_hits": track_total_hits if first_page else False,
                "allow_partial_search_results": False,
            }
            if source is not None:
                search_kwargs["source"] = source
            if source_includes is not None:
                search_kwargs["source_includes"] = list(source_includes)
            if cursor is not None:
                search_kwargs["search_after"] = cursor

            response = client.search(**search_kwargs)
            latest_pit_id = response.get("pit_id")
            if latest_pit_id:
                pit_id = latest_pit_id
            _raise_on_search_failure(response)

            hits = list(response.get("hits", {}).get("hits", []))
            total = _total_value(response) if first_page and track_total_hits else None
            if not hits:
                if first_page and track_total_hits:
                    yield PitSearchPage(total=total or 0, hits=[])
                return

            yield PitSearchPage(total=total, hits=hits)

            next_cursor = hits[-1].get("sort")
            if not next_cursor or list(next_cursor) == cursor:
                raise RuntimeError(
                    "Elasticsearch search_after sort cursor is missing or did not advance"
                )
            cursor = list(next_cursor)
            first_page = False
    finally:
        if pit_id:
            try:
                client.close_point_in_time(id=pit_id)
            except Exception:
                logger.warning("Failed to close Elasticsearch PIT", exc_info=True)


def iter_pit_search_hits(
    client: Elasticsearch,
    **kwargs: Any,
) -> Iterator[dict[str, Any]]:
    pages = iter_pit_search_pages(client, **kwargs)
    try:
        for page in pages:
            yield from page.hits
    finally:
        pages.close()


def pit_search_slice(
    client: Elasticsearch,
    *,
    index: str | Sequence[str],
    query: Mapping[str, Any],
    sort: Sequence[str | Mapping[str, Any]],
    offset: int,
    size: int,
    batch_size: int = DEFAULT_SEARCH_AFTER_BATCH_SIZE,
) -> tuple[int, list[dict[str, Any]]]:
    if offset < 0 or size < 0:
        raise ValueError("offset and size must be non-negative")

    total = 0
    selected: list[dict[str, Any]] = []
    seen = 0
    pages = iter_pit_search_pages(
        client,
        index=index,
        query=query,
        sort=sort,
        batch_size=batch_size,
        track_total_hits=True,
    )
    try:
        for page in pages:
            if page.total is not None:
                total = page.total
            if not page.hits or size == 0:
                break

            page_end = seen + len(page.hits)
            take_start = max(offset - seen, 0)
            take_end = min(offset + size - seen, len(page.hits))
            if take_start < take_end:
                selected.extend(page.hits[take_start:take_end])
            seen = page_end
            if len(selected) >= size or seen >= total:
                break
    finally:
        pages.close()
    return total, selected
