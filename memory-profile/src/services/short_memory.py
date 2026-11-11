"""Short-term aggregation, memo state, and display pagination."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.common.normalization import as_utc as as_utc_aware
from src.common.normalization import datetime_to_epoch_ms as to_timestamp_ms
from src.repositories.short_memory import ShortRepository, ShortTermMemoRepository
from src.schemas.short_memory import (
    MemoDateType,
    MemoScope,
    MemoStatus,
    ShortTermMemoCounts,
    ShortTermMemoGroupCounts,
    ShortTermMemoItem,
    ShortTermMemoList,
)

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


class ShortService:
    _ROLE_LABELS = {"user": "用户", "assistant": "助手", "system": "系统"}

    def __init__(self, runtime: ProfileRuntime):
        self.repository = ShortRepository(runtime)

    async def get_short_databasets(self, end_user_id: str) -> list[dict]:
        short_memories = await self.repository.latest(end_user_id)
        short_result = []
        for memory in short_memories:
            deep_expanded = {}
            messages = memory.messages
            aimessages = memory.aimessages
            retrieved_content = memory.retrieved_content or []

            logger.debug(f"Retrieved content: {retrieved_content}")

            retrieval_source = []
            for item in retrieved_content:
                if isinstance(item, dict):
                    for key, values in item.items():
                        retrieval_source.append(
                            {"query": key, "retrieval": values, "source": "上下文记忆"}
                        )

            deep_expanded["retrieval"] = retrieval_source
            deep_expanded["message"] = messages
            deep_expanded["answer"] = aimessages
            short_result.append(deep_expanded)
        return short_result

    async def get_long_databasets(self, end_user_id: str) -> list[dict]:
        groups = await self.repository.pending_messages(end_user_id)
        long_result = []
        for messages in groups:
            if not messages:
                continue

            # Build a single entry per conversation: grouped messages
            lines = []
            for msg in messages:
                role_label = self._ROLE_LABELS.get(msg.role, msg.role)
                lines.append(f"[{role_label}] {msg.content}")

            retrieval = "\n\n".join(lines)
            # Use the first user message content as the query/title
            first_user = next((m for m in messages if m.role == "user"), messages[0])
            preview = first_user.content[:80] + ("..." if len(first_user.content) > 80 else "")
            query = f"[会话] {preview}"

            long_result.append(
                {
                    "query": query,
                    "retrieval": retrieval,
                }
            )

        return long_result

    async def short_term(self, end_user_id: str) -> dict:
        short_result = await self.get_short_databasets(end_user_id)
        short_count = await self.repository.count(end_user_id)
        long_result = await self.get_long_databasets(end_user_id)
        entity_count = await self.repository.entity_count(end_user_id)
        return {
            "short_term": short_result,
            "long_term": long_result,
            "entity": entity_count,
            "retrieval_number": short_count,
            "long_term_number": len(long_result),
        }

    async def active_user(self, end_user_id, workspace_id):
        return await self.repository.active_user(end_user_id, workspace_id)


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
    def __init__(self, runtime: ProfileRuntime):
        self.repository = ShortTermMemoRepository(runtime)

    async def _find_memos(self, end_user_id: str, now: datetime) -> list[dict[str, Any]]:
        """Use ES first and fall back to Neo4j for missing or failed reads."""
        rows: list[dict[str, Any]] = []
        present = False
        try:
            rows, present = await self.repository.elasticsearch_memos(end_user_id, now)
        except Exception:
            logger.warning("Short-term ES list failed; falling back to Neo4j", exc_info=True)
        if present:
            return rows
        return await self.repository.neo4j_memos(end_user_id, now)

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
        now = as_utc_aware(now) if now is not None else datetime.now(UTC)
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
            items=[memo.to_item(now) for memo in ordered[offset : offset + pagesize]],
            page={
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": page * pagesize < total,
            },
        )
