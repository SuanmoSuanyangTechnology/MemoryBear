"""RAG 记忆统计读取（Dashboard / 记忆管理专用）。

- 数据源固定为 `rag_memory_current` 别名（``MemoryNodeType.RAG_MEMORY``），
  不再经知识库 documents/chunk 统计。
- 所有查询统一排除软删记录（``delete_at`` 不存在）。
- 同步入口使用独立线程 + 独立事件循环执行异步 ES 请求，可在同步控制器
  （FastAPI 线程池）与同步 Celery 任务中直接调用；异步入口直接 await。
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from elasticsearch import AsyncElasticsearch

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.provider.elasticsearch.config import (
    build_elasticsearch_client_config,
)
from app.core.memory.storage.provider.elasticsearch.index import get_index_name
from app.core.utils.datetime_utils import to_iso_z

logger = logging.getLogger(__name__)

INDEX_NAME = get_index_name(MemoryNodeType.RAG_MEMORY)
LEGACY_SOURCE = "legacy"
#: 列表接口单页上限，与控制器 Query 校验保持一致。
MAX_PAGE_SIZE = 100


def _active_clause() -> dict[str, Any]:
    """未软删：与召回口径一致。"""
    return {"bool": {"must_not": [{"exists": {"field": "delete_at"}}]}}


def _build_query(
        end_user_ids: Sequence[str] | None,
        *,
        sources: Sequence[str] | None = None,
        exclude_legacy: bool = False,
        keyword: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
) -> dict[str, Any] | None:
    """组装 bool 查询；``end_user_ids`` 为空列表时返回 None（必然无结果）。"""
    if end_user_ids is not None and not end_user_ids:
        return None

    filters: list[dict[str, Any]] = [_active_clause()]
    must: list[dict[str, Any]] = []

    if end_user_ids is not None:
        filters.append({"terms": {"end_user_id": list(end_user_ids)}})
    if sources:
        filters.append({"terms": {"source": list(sources)}})
    if exclude_legacy:
        filters.append({"bool": {"must_not": [{"term": {"source": LEGACY_SOURCE}}]}})
    if keyword and keyword.strip():
        must.append({"match_phrase": {"content": keyword.strip()}})
    if start is not None or end is not None:
        bounds: dict[str, str] = {}
        if start is not None:
            bounds["gte"] = to_iso_z(start)
        if end is not None:
            bounds["lte"] = to_iso_z(end)
        filters.append({"range": {"dialog_at": bounds}})

    query: dict[str, Any] = {"bool": {"filter": filters}}
    if must:
        query["bool"]["must"] = must
    return query


async def _with_client(fn: Callable[[AsyncElasticsearch], Any]) -> Any:
    client = AsyncElasticsearch(**build_elasticsearch_client_config())
    try:
        return await fn(client)
    finally:
        await client.close()


def _run_sync(coro):
    """在独立线程的事件循环里执行协程，避免与调用方事件循环冲突。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def count_messages(
        end_user_ids: Sequence[str] | None,
        *,
        sources: Sequence[str] | None = None,
        exclude_legacy: bool = False,
        keyword: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
) -> int:
    """按条件统计消息条数。"""
    query = _build_query(
        end_user_ids,
        sources=sources,
        exclude_legacy=exclude_legacy,
        keyword=keyword,
        start=start,
        end=end,
    )
    if query is None:
        return 0

    async def _count(client: AsyncElasticsearch) -> int:
        result = await client.count(index=INDEX_NAME, query=query)
        return int(result.get("count", 0))

    return await _with_client(_count)


def count_messages_sync(end_user_ids: Sequence[str] | None, **kwargs) -> int:
    return _run_sync(count_messages(end_user_ids, **kwargs))


async def count_messages_by_end_user(
        end_user_ids: Sequence[str],
        *,
        exclude_legacy: bool = False,
) -> dict[str, int]:
    """按宿主统计消息条数；未命中的宿主补 0。"""
    if not end_user_ids:
        return {}

    query = _build_query(end_user_ids, exclude_legacy=exclude_legacy)
    assert query is not None

    async def _agg(client: AsyncElasticsearch) -> dict[str, int]:
        result = await client.search(
            index=INDEX_NAME,
            query=query,
            size=0,
            aggs={
                "by_user": {
                    "terms": {
                        "field": "end_user_id",
                        "size": len(end_user_ids),
                    }
                }
            },
        )
        buckets = (result.get("aggregations") or {}).get("by_user", {}).get(
            "buckets", []
        )
        counts = {str(bucket["key"]): int(bucket["doc_count"]) for bucket in buckets}
        for user_id in end_user_ids:
            counts.setdefault(str(user_id), 0)
        return counts

    return await _with_client(_agg)


def count_messages_by_end_user_sync(
        end_user_ids: Sequence[str],
        *,
        exclude_legacy: bool = False,
) -> dict[str, int]:
    return _run_sync(
        count_messages_by_end_user(end_user_ids, exclude_legacy=exclude_legacy)
    )


async def count_messages_by_day(
        end_user_ids: Sequence[str],
        start: datetime,
        end: datetime,
        *,
        exclude_legacy: bool = False,
) -> dict[date, int]:
    """按 UTC 自然日统计新增消息数，缺失日期补 0。"""
    query = _build_query(end_user_ids, exclude_legacy=exclude_legacy)
    if query is None:
        return {}

    async def _agg(client: AsyncElasticsearch) -> dict[date, int]:
        result = await client.search(
            index=INDEX_NAME,
            query=query,
            size=0,
            aggs={
                "per_day": {
                    "date_histogram": {
                        "field": "dialog_at",
                        "calendar_interval": "day",
                        "time_zone": "UTC",
                        "min_doc_count": 0,
                        "extended_bounds": {
                            "min": to_iso_z(start),
                            "max": to_iso_z(end),
                        },
                    }
                }
            },
        )
        buckets = (result.get("aggregations") or {}).get("per_day", {}).get(
            "buckets", []
        )
        return {
            datetime.fromtimestamp(
                int(bucket["key"]) / 1000, tz=timezone.utc
            ).date(): int(bucket["doc_count"])
            for bucket in buckets
        }

    return await _with_client(_agg)


def count_messages_by_day_sync(end_user_ids: Sequence[str], start: datetime, end: datetime, **kwargs) -> dict[date, int]:
    return _run_sync(count_messages_by_day(end_user_ids, start, end, **kwargs))


async def list_messages(
        end_user_ids: Sequence[str] | None,
        *,
        page: int,
        pagesize: int,
        sources: Sequence[str] | None = None,
        keyword: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        order: str = "desc",
) -> dict[str, Any]:
    """按 dialog_at 分页返回消息，默认最新在前。"""
    safe_page = max(int(page), 1)
    safe_pagesize = min(max(int(pagesize), 1), MAX_PAGE_SIZE)
    safe_order = "asc" if str(order or "").strip().lower() == "asc" else "desc"
    query = _build_query(
        end_user_ids,
        sources=sources,
        keyword=keyword,
        start=start,
        end=end,
    )
    if query is None:
        return {
            "page": {
                "page": safe_page,
                "pagesize": safe_pagesize,
                "total": 0,
                "hasnext": False,
            },
            "items": [],
        }

    async def _search(client: AsyncElasticsearch) -> dict[str, Any]:
        result = await client.search(
            index=INDEX_NAME,
            query=query,
            from_=(safe_page - 1) * safe_pagesize,
            size=safe_pagesize,
            sort=[
                {"dialog_at": {"order": safe_order, "missing": "_last"}},
                {"id": {"order": "asc"}},
            ],
            _source=["id", "role", "content", "dialog_at", "source"],
            track_total_hits=True,
        )
        total = int((result.get("hits") or {}).get("total", {}).get("value", 0))
        items = []
        for hit in (result.get("hits") or {}).get("hits", []):
            source = hit.get("_source") or {}
            origin = str(source.get("source") or "")
            items.append(
                {
                    "id": str(source.get("id") or hit.get("_id") or ""),
                    "role": source.get("role") or "",
                    "content": source.get("content") or "",
                    "dialog_at": source.get("dialog_at") or "",
                    "source": origin,
                    "legacy": origin == LEGACY_SOURCE,
                }
            )
        return {
            "page": {
                "page": safe_page,
                "pagesize": safe_pagesize,
                "total": total,
                "hasnext": safe_page * safe_pagesize < total,
            },
            "items": items,
        }

    return await _with_client(_search)


def list_messages_sync(end_user_ids: Sequence[str] | None, **kwargs) -> dict[str, Any]:
    return _run_sync(list_messages(end_user_ids, **kwargs))


async def daily_totals(
        end_user_ids: Sequence[str],
        start: datetime,
        end: datetime,
        *,
        exclude_legacy: bool = True,
) -> list[tuple[date, int]]:
    """返回区间内逐日累计总量（用于记忆增长趋势），按日期升序。"""
    if not end_user_ids:
        return []

    start_day = start.date() if isinstance(start, datetime) else start
    end_day = end.date() if isinstance(end, datetime) else end
    if end_day < start_day:
        return []

    counts = await count_messages_by_day(
        end_user_ids,
        datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc),
        datetime(
            end_day.year,
            end_day.month,
            end_day.day,
            23,
            59,
            59,
            999999,
            tzinfo=timezone.utc,
        ),
        exclude_legacy=exclude_legacy,
    )

    base_before_start = await count_messages(
        end_user_ids,
        end=datetime(
            start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc
        ) - timedelta(microseconds=1),
        exclude_legacy=exclude_legacy,
    )

    totals: list[tuple[date, int]] = []
    running = base_before_start
    current = start_day
    while current <= end_day:
        running += counts.get(current, 0)
        totals.append((current, running))
        current += timedelta(days=1)
    return totals


def daily_totals_sync(end_user_ids: Sequence[str], start: datetime, end: datetime, **kwargs) -> list[tuple[date, int]]:
    return _run_sync(daily_totals(end_user_ids, start, end, **kwargs))
