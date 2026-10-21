"""Memo state, response mapping, and in-memory display pagination."""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.utils.datetime_utils import as_utc_aware, to_timestamp_ms, utcnow
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage.custom.workspace_statistics import (
    MemoQueryExecutor,
    get_elasticsearch_short_term_counts,
    get_elasticsearch_short_term_memos,
    get_neo4j_short_term_count,
    get_neo4j_short_term_memos,
)
from app.schemas.short_term_memo_schema import (
    MemoDateType,
    MemoScope,
    MemoStatus,
    ShortTermMemoCounts,
    ShortTermMemoGroupCounts,
    ShortTermMemoItem,
    ShortTermMemoList,
)

logger = logging.getLogger(__name__)


def _utc_datetime(value: Any) -> datetime | None:
    """Normalize datetime projections from either storage backend to UTC."""
    if value is None:
        return None
    if hasattr(value, "to_native"):
        value = value.to_native()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Expected an aware datetime from memo projection")
    return as_utc_aware(value)


@dataclass(frozen=True)
class _Memo:
    id: str
    statement: str
    start: datetime | None
    end: datetime | None
    mentioned: datetime | None
    unspecified: bool
    status: MemoStatus

    @property
    def retain_until(self) -> datetime | None:
        return self.start + timedelta(hours=72) if self.end is None else None

    def sort_key(self) -> tuple[int, float, str]:
        if self.status == "ended":
            return (0, -self.end.timestamp(), self.id)
        if self.unspecified:
            return (1, -self.mentioned.timestamp(), self.id)
        if self.status == "pending":
            reference = self.start
        elif self.end is not None:
            reference = self.end
        else:
            reference = self.retain_until
        return (0, reference.timestamp(), self.id)

    def to_item(self, now: datetime) -> ShortTermMemoItem:
        if self.status == "pending":
            days = math.ceil((self.start - now) / timedelta(hours=24))
        elif self.status == "ended":
            days = math.floor((now - self.end) / timedelta(hours=24))
        else:
            target = self.end if self.end is not None else self.retain_until
            days = math.ceil((target - now) / timedelta(hours=24))
        return ShortTermMemoItem(
            id=self.id,
            statement=self.statement,
            status=self.status,
            start_date=to_timestamp_ms(self.start),
            end_date=to_timestamp_ms(self.end),
            is_time_unspecified=self.unspecified,
            retain_until=to_timestamp_ms(self.retain_until),
            relative_days=days,
        )


class ShortTermMemoService:
    def __init__(self, neo4j_executor: MemoQueryExecutor | None = None):
        self.neo4j_executor = neo4j_executor

    async def _find_memos(self, end_user_id: str, now: datetime) -> list[dict[str, Any]]:
        """Use ES first; connect and close a Neo4j client only on fallback."""
        elastic_client = ElasticClient()
        rows: list[dict[str, Any]] = []
        present = False
        try:
            elastic_client.client = await elastic_client.connect()
            rows, present = await get_elasticsearch_short_term_memos(
                elastic_client, end_user_id, now,
            )
        except Exception:
            logger.warning("Short-term ES list failed; falling back to Neo4j", exc_info=True)
        finally:
            try:
                await elastic_client.close()
            except Exception:
                logger.exception("Failed to close short-term list Elasticsearch client")
        if present:
            return rows
        if self.neo4j_executor is not None:
            return await get_neo4j_short_term_memos(self.neo4j_executor, end_user_id, now)
        neo4j_client = Neo4jClient()
        try:
            neo4j_client.client = await neo4j_client.connect()
            return await get_neo4j_short_term_memos(neo4j_client, end_user_id, now)
        finally:
            try:
                await neo4j_client.close()
            except Exception:
                logger.exception("Failed to close short-term list Neo4j client")

    async def list_memos(
        self,
        end_user_id: str,
        scope: MemoScope,
        date_type: MemoDateType,
        page: int,
        pagesize: int,
        *,
        now: datetime | None = None,
    ) -> ShortTermMemoList:
        """Count unique eligible memos before filtering, sorting and paging the list."""
        now = as_utc_aware(now) if now is not None else utcnow()
        rows = await self._find_memos(end_user_id, now)
        memos: dict[str, _Memo] = {}
        for row in rows:
            start = _utc_datetime(row["start_at"])
            end = _utc_datetime(row["end_at"])
            if start is not None and now < start:
                status: MemoStatus = "pending"
            elif end is not None and now > end:
                status = "ended"
            else:
                status = "ongoing"
            unspecified = row["is_time_unspecified"]
            memo = _Memo(
                id=str(row["id"]),
                statement=row["statement"],
                start=start,
                end=end,
                mentioned=_utc_datetime(row["mentioned_at"]),
                unspecified=unspecified,
                status=status,
            )
            memos.setdefault(memo.id, memo)
        counts = ShortTermMemoCounts(
            total=len(memos),
            current=ShortTermMemoGroupCounts(total=0, dated=0, undated=0),
            ended=ShortTermMemoGroupCounts(total=0, dated=0, undated=0),
        )
        selected: list[_Memo] = []
        for memo in memos.values():
            group = counts.ended if memo.status == "ended" else counts.current
            group.total += 1
            if memo.unspecified:
                group.undated += 1
            else:
                group.dated += 1

            if (scope == "ended") != (memo.status == "ended"):
                continue
            if date_type == "dated" and memo.unspecified:
                continue
            if date_type == "undated" and not memo.unspecified:
                continue
            selected.append(memo)
        ordered = sorted(selected, key=lambda memo: memo.sort_key())
        total = len(ordered)
        offset = (page - 1) * pagesize
        return ShortTermMemoList(
            counts=counts,
            items=[memo.to_item(now) for memo in ordered[offset:offset + pagesize]],
            page={
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": page * pagesize < total,
            },
        )


async def count_short_term_memos(end_user_id: str, *, now: datetime | None = None) -> int:
    """Count user memos from ES first, opening Neo4j only when fallback is needed."""
    elastic_client = ElasticClient()
    try:
        return await count_short_term_memos_for_users(
            [end_user_id], elastic_client=elastic_client, now=now,
        )
    finally:
        try:
            await elastic_client.close()
        except Exception:
            logger.exception("Failed to close short-term count Elasticsearch client")


async def count_short_term_memos_for_users(
    end_user_ids: list[str],
    *,
    elastic_client: ElasticClient,
    neo4j_executor: MemoQueryExecutor | None = None,
    now: datetime | None = None,
) -> int:
    """Count via ES, keeping valid zeros and falling back only for missing users.

    Workspace callers lend their clients; user requests connect and close their
    own Neo4j client on fallback. An ES failure sends the entire batch to Neo4j.
    """
    if not end_user_ids:
        return 0
    now = as_utc_aware(now) if now is not None else utcnow()
    try:
        if elastic_client.client is None:
            elastic_client.client = await elastic_client.connect()
        counts, present_ids = await get_elasticsearch_short_term_counts(
            elastic_client, end_user_ids, now,
        )
    except Exception:
        logger.warning("Short-term ES count failed; falling back to Neo4j", exc_info=True)
        total = 0
        missing_ids = end_user_ids
    else:
        total = sum(counts[user_id] for user_id in present_ids)
        missing_ids = [user_id for user_id in end_user_ids if user_id not in present_ids]
    if missing_ids:
        if neo4j_executor is not None:
            total += await get_neo4j_short_term_count(neo4j_executor, missing_ids, now)
        else:
            neo4j_client = Neo4jClient()
            try:
                neo4j_client.client = await neo4j_client.connect()
                total += await get_neo4j_short_term_count(neo4j_client, missing_ids, now)
            finally:
                try:
                    await neo4j_client.close()
                except Exception:
                    logger.exception("Failed to close short-term count Neo4j client")
    return total
