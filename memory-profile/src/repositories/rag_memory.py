"""Read-only RAG-message index adapter using the writer's active-document policy."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from src.infrastructure.elasticsearch import ElasticsearchManager
from src.repositories.search import _validate_response

INDEX = "rag_memory_current"


def query_for(
    ids: list[str],
    *,
    sources: list[str] | None = None,
    keyword: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    exclude_legacy: bool = False,
) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [{"terms": {"end_user_id": ids}}]
    body: dict[str, Any] = {"filter": filters, "must_not": [{"exists": {"field": "delete_at"}}]}
    if sources:
        filters.append({"terms": {"source": sources}})
    if exclude_legacy:
        body["must_not"].append({"term": {"source": "legacy"}})
    if keyword and keyword.strip():
        body["must"] = [{"match_phrase": {"content": keyword.strip()}}]
    bounds = {}
    for key, value in (("gte", start), ("lte", end)):
        if value is not None:
            bounds[key] = (
                value.replace(tzinfo=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            )
    if bounds:
        filters.append({"range": {"dialog_at": bounds}})
    return {"bool": body}


class RAGMemoryRepository:
    def __init__(self, elasticsearch: ElasticsearchManager) -> None:
        self.elasticsearch = elasticsearch

    async def counts(self, ids: list[str]) -> dict[str, int]:
        if not ids:
            return {}
        result: dict[str, int] = {}
        client = await self.elasticsearch.client()
        # Bounded user batches; aggregate size covers every id in a batch.
        for offset in range(0, len(ids), 1000):
            batch = ids[offset : offset + 1000]
            response = await client.search(
                index=INDEX,
                size=0,
                query=query_for(batch),
                aggregations={"users": {"terms": {"field": "end_user_id", "size": len(batch)}}},
                allow_partial_search_results=False,
            )
            _validate_response(response, "rag counts")
            result.update(
                {
                    str(bucket["key"]): int(bucket["doc_count"])
                    for bucket in response["aggregations"]["users"]["buckets"]
                }
            )
        return result

    async def messages(self, ids: list[str], page: int, pagesize: int, **filters) -> dict[str, Any]:
        if page * pagesize > 10000:
            raise ValueError("page too deep: exceeds ES max_result_window 10000")
        order = filters.pop("order", "asc")
        client = await self.elasticsearch.client()
        response = await client.search(
            index=INDEX,
            query=query_for(ids, **filters),
            from_=(page - 1) * pagesize,
            size=pagesize,
            sort=[
                {"dialog_at": {"order": order, "missing": "_last"}},
                {"role_rank": {"order": "asc", "missing": "_last"}},
                {"id": {"order": "asc"}},
            ],
            source_includes=["id", "role", "content", "dialog_at", "source"],
            track_total_hits=True,
            allow_partial_search_results=False,
        )
        _validate_response(response, "rag messages")
        total = response["hits"]["total"]
        total = int(total["value"] if isinstance(total, dict) else total)
        items = []
        for hit in response["hits"]["hits"]:
            value = hit.get("_source")
            value = value if isinstance(value, dict) else {}
            source = str(value.get("source") or "")
            items.append(
                {
                    "id": str(value.get("id") or hit.get("_id") or ""),
                    "role": value.get("role") or "",
                    "content": value.get("content") or "",
                    "dialog_at": value.get("dialog_at") or "",
                    "source": source,
                    "legacy": source == "legacy",
                }
            )
        return {
            "items": items,
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": page * pagesize < total,
            },
        }

    async def daily_totals(
        self, ids: list[str], start: datetime, end: datetime
    ) -> list[dict[str, int]]:
        if not ids:
            return []
        first = datetime.combine(start.date(), time.min)
        last = datetime.combine(end.date() + timedelta(days=1), time.min) - timedelta(
            milliseconds=1
        )
        client = await self.elasticsearch.client()
        response = await client.search(
            index=INDEX,
            size=0,
            query=query_for(ids, start=first, end=last, exclude_legacy=True),
            aggregations={
                "days": {
                    "date_histogram": {
                        "field": "dialog_at",
                        "calendar_interval": "day",
                        "time_zone": "UTC",
                        "min_doc_count": 0,
                    }
                }
            },
            allow_partial_search_results=False,
        )
        _validate_response(response, "rag daily totals")
        base = await client.count(
            index=INDEX,
            query=query_for(ids, end=first - timedelta(milliseconds=1), exclude_legacy=True),
        )
        _validate_response(base, "rag baseline")
        counts = {
            int(bucket["key"]): int(bucket["doc_count"])
            for bucket in response["aggregations"]["days"]["buckets"]
        }
        total = int(base["count"])
        items = []
        current = first
        while current <= last:
            stamp = int(current.replace(tzinfo=UTC).timestamp() * 1000)
            total += counts.get(stamp, 0)
            items.append({"date": stamp, "total_num": total})
            current += timedelta(days=1)
        return items

    async def total(self, ids: list[str], *, end: datetime | None = None) -> int:
        if not ids:
            return 0
        client = await self.elasticsearch.client()
        response = await client.count(index=INDEX, query=query_for(ids, end=end))
        _validate_response(response, "rag total")
        return int(response["count"])
