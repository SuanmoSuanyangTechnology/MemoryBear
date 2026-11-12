"""Read-only queries for short-term memory and Statement memos."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from numbers import Integral
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from src.models.references.end_user_model import EndUser
from src.models.references.memory_statistics_model import (
    Conversation,
    MemoryMessage,
    ShortTermMemory,
)
from src.repositories.graph import _ELIGIBLE_STATEMENTS
from src.repositories.search import _INDEX_STATEMENT, _short_term_body, _validate_response

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

_SHORT_TERM_MEMO_BATCH_SIZE = 500
_SHORT_TERM_MEMOS_QUERY = (
    _ELIGIBLE_STATEMENTS
    + """
RETURN s.id AS id, s.statement AS statement,
       start_at, end_at, mentioned_at, is_time_unspecified
"""
)


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


class ShortRepository:
    def __init__(self, runtime: ProfileRuntime):
        self.runtime = runtime

    async def latest(self, end_user_id: str):
        async with self.runtime.postgres.session() as session:
            result = await session.execute(
                select(
                    ShortTermMemory.messages,
                    ShortTermMemory.aimessages,
                    ShortTermMemory.retrieved_content,
                )
                .where(ShortTermMemory.end_user_id == end_user_id)
                .order_by(ShortTermMemory.created_at.desc())
                .limit(3)
            )
            return list(result.all())

    async def count(self, end_user_id: str) -> int:
        async with self.runtime.postgres.session() as session:
            return (
                await session.execute(
                    select(func.count())
                    .select_from(ShortTermMemory)
                    .where(ShortTermMemory.end_user_id == end_user_id)
                )
            ).scalar_one()

    async def pending_messages(self, end_user_id: str):
        async with self.runtime.postgres.session() as session:
            conversations = (
                await session.execute(
                    select(Conversation.id, Conversation.write_cursor)
                    .where(Conversation.user_id == end_user_id)
                    .order_by(Conversation.updated_at.desc())
                )
            ).all()
            groups = []
            for conv in conversations:
                messages = (
                    await session.execute(
                        select(MemoryMessage.role, MemoryMessage.content)
                        .where(
                            MemoryMessage.conversation_id == conv.id,
                            MemoryMessage.message_seq > (conv.write_cursor or 0),
                        )
                        .order_by(MemoryMessage.message_seq.asc())
                    )
                ).all()
                groups.append(messages)
            return groups

    async def entity_count(self, end_user_id: str) -> int:
        rows = await self.runtime.neo4j.read_query(
            "MATCH (n:ExtractedEntity) WHERE n.end_user_id = $end_user_id "
            "AND n.delete_at is NULL RETURN COUNT(n) AS num",
            end_user_id=end_user_id,
        )
        return rows[0]["num"]

    async def active_user(self, end_user_id, workspace_id):
        async with self.runtime.postgres.session() as session:
            return (
                await session.execute(
                    select(EndUser.id).where(
                        EndUser.id == end_user_id,
                        EndUser.workspace_id == workspace_id,
                        EndUser.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()


class ShortTermMemoRepository:
    def __init__(self, runtime: ProfileRuntime):
        self.runtime = runtime

    async def neo4j_memos(self, end_user_id: str, now: datetime) -> list[dict[str, Any]]:
        return await self.runtime.neo4j.read_query(
            _SHORT_TERM_MEMOS_QUERY,
            end_user_ids=[end_user_id],
            now=now,
            window_start=now - timedelta(hours=72),
            window_end=now + timedelta(hours=72),
        )

    async def elasticsearch_memos(
        self,
        end_user_id: str,
        now: datetime,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Read all eligible Statements in a PIT; distinguish no data from zero eligible.

        The presence aggregation runs before post_filter, so an existing user with
        no eligible memos does not fall back. PIT batches are internal reads; display
        sorting and page/pagesize pagination remain in the service.
        """
        client = await self.runtime.elasticsearch.client()
        body = _short_term_body([end_user_id], now)
        eligible_filter = body.pop("aggs")["by_end_user"]["aggs"]["short_term"]["filter"]
        body.update(
            {
                "size": _SHORT_TERM_MEMO_BATCH_SIZE,
                "sort": ["_shard_doc"],
                "track_total_hits": False,
                "_source": ["id", "statement"],
                "fields": [
                    {"field": name, "format": "epoch_millis"}
                    for name in ("valid_at", "invalid_at", "dialog_at", "created_at")
                ],
                "post_filter": eligible_filter,
                "aggs": {"statement_presence": {"filter": {"match_all": {}}}},
            }
        )
        opened = await client.open_point_in_time(
            index=_INDEX_STATEMENT,
            keep_alive="1m",
        )
        pit_id = opened.get("id")
        if not isinstance(pit_id, str) or not pit_id:
            raise RuntimeError("Elasticsearch returned no PIT ID for short-term memos")
        rows: list[dict[str, Any]] = []
        present = False
        first_page = True
        try:
            _validate_response(opened, "short-term PIT")
            while True:
                body["pit"] = {"id": pit_id, "keep_alive": "1m"}
                response = await client.search(body=body, allow_partial_search_results=False)
                pit_id = response.get("pit_id") or pit_id
                _validate_response(response, "short-term memo list")
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
            try:
                await client.close_point_in_time(id=pit_id)
            except Exception:
                logger.warning("Failed to close short-term memo PIT", exc_info=True)
        return rows, present
