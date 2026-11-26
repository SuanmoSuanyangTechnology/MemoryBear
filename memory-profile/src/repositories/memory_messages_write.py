"""Writable access to the API-owned ``memory_messages`` table.

The write path used to run inside the API; this service now serves the three
write faces, while the table's DDL stays with the API and the API's workers
consume the rows written here. The row mapping lives in
``models.references.memory_statistics_model``; this module only decides how a
batch is allocated and persisted, and mirrors the API's rules exactly — the
same ``message_seq`` allocation under the same transaction-scoped advisory
lock, the same ``created_at`` / ``dialog_at`` monotonic bump inside one batch,
and the same returned dict — so a row written here is indistinguishable from
one the API wrote and the workers cannot tell which service produced it.

Only the operations the three write faces need live here: batch write, the
conversation ``write_cursor`` pair, and the sliding-window context queries.
Everything else about the table stays with the API.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.datetime_utils import (
    ensure_dialog_at,
    parse_iso_to_utc_naive,
    to_iso_z,
    utcnow_naive,
)
from src.models.references.conversation_model import Conversation
from src.models.references.memory_statistics_model import MemoryMessage

#: Timestamp step forced between rows of one batch. Second-level clocks (and a
#: caller that sends the same timestamp twice) otherwise leave the display
#: query — which orders by time with no secondary key — flipping user and
#: assistant. Same value the API uses.
_BATCH_TS_STEP = timedelta(milliseconds=1)

#: The sliding window the API applies to agent and workflow dispatch.
WINDOW_SIZE = 3


def _as_aware_utc(value: datetime) -> datetime:
    """Read any datetime as aware UTC; used for comparison only."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bump_monotonic_datetime(
    previous: datetime | None, value: datetime
) -> tuple[datetime, datetime]:
    """Force ``value`` strictly later than ``previous`` (``previous + 1ms``).

    The returned value keeps the caller's tz style (naive in, naive out) so a
    caller's timezone semantics are never rewritten silently — the API's rule.
    """

    value_aware = _as_aware_utc(value)
    if previous is not None and value_aware <= previous:
        value_aware = previous + _BATCH_TS_STEP
        if value.tzinfo is None:
            value = value_aware.replace(tzinfo=None)
        else:
            value = value_aware.astimezone(value.tzinfo)
    return value, value_aware


def message_to_dict(message: MemoryMessage) -> dict[str, Any]:
    """Serialize one row for the pipeline payload (the API's shape)."""

    return {
        "memory_message_id": str(message.id),
        "role": message.role,
        "source": message.source,
        "content": message.content,
        "message_seq": int(message.message_seq),
        "should_memorize": bool(message.should_memorize),
        "created_at": to_iso_z(message.created_at) if message.created_at else None,
        "dialog_at": message.dialog_at,
        "files": message.files,
        "pruned_content": message.pruned_content,
        "topic_entity_hint": message.topic_entity_hint,
        "scene_boundary": message.scene_boundary,
        "scene_summary_claimed_at": (
            to_iso_z(message.scene_summary_claimed_at)
            if message.scene_summary_claimed_at
            else None
        ),
    }


class MemoryMessageWriteRepository:
    """Write and read the rows the three memory-write faces produce."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── write ────────────────────────────────────────────────────────────

    async def write_batch(
        self,
        *,
        conversation_id: str | None,
        messages: Sequence[Mapping[str, Any]],
        end_user_id: str,
        source: str,
    ) -> list[dict[str, Any]]:
        """Insert one batch, allocating consecutive ``message_seq`` values.

        The caller owns the transaction. An empty ``content`` row is skipped
        rather than rejected: every entry point filters the same way before it
        pairs a written row back with its raw message.
        """

        async with self._seq_lock(conversation_id, end_user_id, source):
            return await self._write_batch_inner(
                conversation_id=conversation_id,
                messages=messages,
                end_user_id=end_user_id,
                source=source,
            )

    @asynccontextmanager
    async def _seq_lock(
        self, conversation_id: str | None, end_user_id: str, source: str
    ) -> AsyncIterator[None]:
        """Serialize seq allocation on the group the batch belongs to.

        ``pg_advisory_xact_lock`` releases on commit/rollback, so no explicit
        unlock exists. The key is normalized through ``uuid.UUID`` so two
        spellings of one conversation cannot take different locks and collide.
        """

        if conversation_id is not None:
            lock_key = f"mm_seq:conv:{uuid.UUID(str(conversation_id))}"
        else:
            lock_key = f"mm_seq:{end_user_id}:{source}"
        await self._session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )
        yield

    async def _next_seq(
        self, *, conversation_id: str | None, end_user_id: str, source: str
    ) -> int:
        statement = sa.select(sa.func.coalesce(sa.func.max(MemoryMessage.message_seq), 0))
        if conversation_id is not None:
            statement = statement.where(
                MemoryMessage.conversation_id == uuid.UUID(str(conversation_id))
            )
        else:
            statement = statement.where(
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.end_user_id == end_user_id,
                MemoryMessage.source == source,
            )
        value = (await self._session.execute(statement)).scalar()
        return int(value or 0)

    async def _write_batch_inner(
        self,
        *,
        conversation_id: str | None,
        messages: Sequence[Mapping[str, Any]],
        end_user_id: str,
        source: str,
    ) -> list[dict[str, Any]]:
        next_seq = await self._next_seq(
            conversation_id=conversation_id, end_user_id=end_user_id, source=source
        )
        conv_uuid = uuid.UUID(str(conversation_id)) if conversation_id else None

        written: list[dict[str, Any]] = []
        previous_created: datetime | None = None
        previous_dialog: datetime | None = None
        rows: list[dict[str, Any]] = []

        for message in messages:
            role = str(message.get("role", "user"))
            content = str(message.get("content", "") or "")
            if not content.strip():
                continue

            next_seq += 1
            created_at, previous_created = _bump_monotonic_datetime(
                previous_created, message.get("created_at") or utcnow_naive()
            )
            dialog_at = ensure_dialog_at(message.get("dialog_at"))
            dialog_naive = parse_iso_to_utc_naive(dialog_at)
            if dialog_naive is not None:
                if previous_dialog is not None and dialog_naive <= previous_dialog:
                    dialog_naive = previous_dialog + _BATCH_TS_STEP
                    dialog_at = dialog_naive.replace(tzinfo=UTC).isoformat()
                previous_dialog = dialog_naive

            row = {
                "id": uuid.uuid4(),
                "conversation_id": conv_uuid,
                "original_message_id": message.get("original_message_id"),
                "end_user_id": end_user_id,
                "source": source,
                "role": role,
                "content": content,
                "message_seq": next_seq,
                "should_memorize": bool(message.get("should_memorize", True)),
                "files": message.get("files"),
                "dialog_at": dialog_at,
                "created_at": created_at,
            }
            rows.append(row)
            written.append(
                {
                    "memory_message_id": str(row["id"]),
                    "role": role,
                    "source": source,
                    "message_seq": next_seq,
                    "content": content,
                    "dialog_at": dialog_at,
                    "files": row["files"],
                    # The fast write uses this as its emotion-cache key.
                    "original_message_id": (
                        str(row["original_message_id"])
                        if row["original_message_id"]
                        else None
                    ),
                    "should_memorize": row["should_memorize"],
                }
            )

        if rows:
            await self._session.execute(sa.insert(MemoryMessage), rows)
        return written

    # ── conversation cursor (agent / workflow) ───────────────────────────

    async def write_cursor(self, conversation_id: str) -> int | None:
        """The conversation's ``write_cursor``, or ``None`` when it is gone."""

        statement = sa.select(Conversation.write_cursor).where(
            Conversation.id == uuid.UUID(str(conversation_id))
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def advance_write_cursor(self, conversation_id: str, message_seq: int) -> bool:
        """Advance the cursor monotonically; ``False`` means already advanced."""

        statement = (
            sa.update(Conversation)
            .where(
                Conversation.id == uuid.UUID(str(conversation_id)),
                Conversation.write_cursor < message_seq,
            )
            .values(write_cursor=message_seq)
        )
        result = await self._session.execute(statement)
        return bool(result.rowcount and result.rowcount > 0)

    async def pending_messages(
        self, conversation_id: str, write_cursor: int
    ) -> list[dict[str, Any]]:
        """Rows the cursor has not passed yet, in seq order."""

        statement = (
            sa.select(MemoryMessage)
            .where(
                MemoryMessage.conversation_id == uuid.UUID(str(conversation_id)),
                MemoryMessage.message_seq > write_cursor,
            )
            .order_by(MemoryMessage.message_seq.asc())
        )
        messages = (await self._session.execute(statement)).scalars().all()
        return [message_to_dict(message) for message in messages]

    async def user_seqs(self, conversation_id: str) -> list[int]:
        """Every ``role='user'`` seq of the conversation, ascending."""

        statement = (
            sa.select(MemoryMessage.message_seq)
            .where(
                MemoryMessage.conversation_id == uuid.UUID(str(conversation_id)),
                MemoryMessage.role == "user",
            )
            .order_by(MemoryMessage.message_seq.asc())
        )
        values = (await self._session.execute(statement)).scalars().all()
        return [int(value) for value in values if value is not None]

    async def seq_group(self, conversation_id: str, message_seq: int) -> list[dict[str, Any]]:
        """All rows sharing one seq, oldest first (dirty history may collide)."""

        statement = (
            sa.select(MemoryMessage)
            .where(
                MemoryMessage.conversation_id == uuid.UUID(str(conversation_id)),
                MemoryMessage.message_seq == message_seq,
            )
            .order_by(MemoryMessage.created_at.asc(), MemoryMessage.id.asc())
        )
        messages = (await self._session.execute(statement)).scalars().all()
        return [message_to_dict(message) for message in messages]

    # ── context windows (agent / workflow) ───────────────────────────────

    async def context_before(
        self, conversation_id: str, target_seq: int, window_size: int = WINDOW_SIZE
    ) -> list[dict[str, Any]]:
        """Everything from the earliest user message in the window up to the target."""

        upstream = (
            await self._session.execute(
                sa.select(MemoryMessage.message_seq)
                .where(
                    MemoryMessage.conversation_id == uuid.UUID(str(conversation_id)),
                    MemoryMessage.role == "user",
                    MemoryMessage.message_seq < target_seq,
                )
                .order_by(MemoryMessage.message_seq.desc())
                .limit(window_size)
            )
        ).scalars().all()
        if not upstream:
            return []
        upper_bound = min(int(value) for value in upstream)
        statement = (
            sa.select(MemoryMessage)
            .where(
                MemoryMessage.conversation_id == uuid.UUID(str(conversation_id)),
                MemoryMessage.message_seq >= upper_bound,
                MemoryMessage.message_seq < target_seq,
            )
            .order_by(
                MemoryMessage.message_seq.asc(),
                MemoryMessage.created_at.asc(),
                MemoryMessage.id.asc(),
            )
        )
        messages = (await self._session.execute(statement)).scalars().all()
        return [message_to_dict(message) for message in messages]

    async def context_after(
        self, conversation_id: str, target_seq: int, window_size: int = WINDOW_SIZE
    ) -> list[dict[str, Any]]:
        """Everything up to the last user message in the window, else the tail.

        A conversation that ends with assistant messages keeps them as context:
        the API returns every row after the target when no user message follows.
        """

        downstream = (
            await self._session.execute(
                sa.select(MemoryMessage.message_seq)
                .where(
                    MemoryMessage.conversation_id == uuid.UUID(str(conversation_id)),
                    MemoryMessage.role == "user",
                    MemoryMessage.message_seq > target_seq,
                )
                .order_by(MemoryMessage.message_seq.asc())
                .limit(window_size)
            )
        ).scalars().all()

        statement = sa.select(MemoryMessage).where(
            MemoryMessage.conversation_id == uuid.UUID(str(conversation_id)),
            MemoryMessage.message_seq > target_seq,
        )
        if downstream:
            lower_bound = max(int(value) for value in downstream)
            statement = statement.where(MemoryMessage.message_seq <= lower_bound)
        statement = statement.order_by(
            MemoryMessage.message_seq.asc(),
            MemoryMessage.created_at.asc(),
            MemoryMessage.id.asc(),
        )
        messages = (await self._session.execute(statement)).scalars().all()
        return [message_to_dict(message) for message in messages]


__all__ = [
    "MemoryMessageWriteRepository",
    "WINDOW_SIZE",
    "message_to_dict",
]
