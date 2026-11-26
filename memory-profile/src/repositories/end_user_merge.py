"""PostgreSQL operations of an end-user merge.

Every method takes the caller's ``AsyncSession`` so the merge can commit the
soft-delete, the ``end_user_merge`` intent and the target profile in one
transaction — the API's central invariant. DDL stays with the API.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.timing import utcnow_naive
from src.models.references.conversation_model import Conversation
from src.models.references.end_user_model import EndUser, EndUserMerge
from src.models.references.memory_display_model import (
    MemoryDisplayRecord,
    MemoryEngineDisplayEvent,
)
from src.models.references.memory_forget_model import ForgettingCycleHistory
from src.models.references.memory_generation_model import EndUserInfo
from src.models.references.memory_reflection_log_model import MemoryReflectionLog
from src.models.references.memory_statistics_model import (
    DialogueEmotionRaw,
    ForgetAudit,
    LongTermMemory,
    MemoryMessage,
    MemoryPerceptual,
    ShortTermMemory,
)

#: Rows per UPDATE batch; keeps a large merge from locking a whole table.
_REFERENCE_BATCH = 200


class EndUserMergeRepository:
    @staticmethod
    async def get_end_user_any(
        session: AsyncSession, end_user_id: uuid.UUID
    ) -> EndUser | None:
        result = await session.execute(select_end_user(end_user_id))
        return result.scalars().first()

    @staticmethod
    async def get_end_user_info(
        session: AsyncSession, end_user_id: uuid.UUID
    ) -> EndUserInfo | None:
        result = await session.execute(
            sa.select(EndUserInfo)
            .where(EndUserInfo.end_user_id == end_user_id)
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    async def get_merge_target_id(
        session: AsyncSession, origin_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> uuid.UUID | None:
        result = await session.execute(
            sa.select(EndUserMerge.target_id)
            .where(
                EndUserMerge.origin_id == origin_id,
                EndUserMerge.workspace_id == workspace_id,
            )
            .order_by(EndUserMerge.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    async def soft_delete_many(
        session: AsyncSession, end_user_ids: set[uuid.UUID]
    ) -> int:
        """``is_active = False`` for the sources; never commits."""

        if not end_user_ids:
            return 0
        result = await session.execute(
            sa.update(EndUser)
            .where(EndUser.id.in_(end_user_ids), EndUser.is_active.is_(True))
            .values(is_active=False)
        )
        return result.rowcount or 0

    @staticmethod
    async def flatten_merge_chain(
        session: AsyncSession,
        source_ids: set[uuid.UUID],
        target_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> None:
        """Re-point historical ``B -> A`` edges at ``C`` when ``A -> C``."""

        for source_id in source_ids:
            await session.execute(
                sa.update(EndUserMerge)
                .where(
                    EndUserMerge.target_id == source_id,
                    EndUserMerge.workspace_id == workspace_id,
                )
                .values(target_id=target_id)
            )

    @staticmethod
    def add_merge_record(
        session: AsyncSession,
        *,
        origin_id: uuid.UUID,
        origin_other_id: str,
        target_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> None:
        session.add(
            EndUserMerge(
                workspace_id=workspace_id,
                origin_id=origin_id,
                origin_other_id=origin_other_id,
                target_id=target_id,
            )
        )

    @staticmethod
    async def migrate_references(
        session: AsyncSession, src_id: uuid.UUID, target_id: uuid.UUID
    ) -> dict[str, int]:
        """Move ``end_user_id`` in every reference table from source to target.

        Mirrors the API's table list and value types: string columns take the
        text form, UUID columns the UUID object.
        """

        src_str, tgt_str = str(src_id), str(target_id)
        tables: list[tuple[type, str, object, object]] = [
            (Conversation, "user_id", src_str, tgt_str),
            (MemoryMessage, "end_user_id", src_str, tgt_str),
            (ShortTermMemory, "end_user_id", src_str, tgt_str),
            (LongTermMemory, "end_user_id", src_str, tgt_str),
            (ForgettingCycleHistory, "end_user_id", src_str, tgt_str),
            (MemoryDisplayRecord, "end_user_id", src_id, target_id),
            (MemoryEngineDisplayEvent, "end_user_id", src_id, target_id),
            (ForgetAudit, "end_user_id", src_id, target_id),
            (MemoryPerceptual, "end_user_id", src_id, target_id),
            (MemoryReflectionLog, "end_user_id", src_id, target_id),
            (DialogueEmotionRaw, "end_user_id", src_id, target_id),
        ]

        stats: dict[str, int] = {}
        for model, column_name, source_value, target_value in tables:
            column = getattr(model, column_name)
            total = 0
            while True:
                pk_result = await session.execute(
                    sa.select(model.id)
                    .where(column == source_value)
                    .limit(_REFERENCE_BATCH)
                )
                ids = list(pk_result.scalars().all())
                if not ids:
                    break
                result = await session.execute(
                    sa.update(model)
                    .where(model.id.in_(ids))
                    .values(**{column_name: target_value})
                )
                total += result.rowcount or 0
            if total:
                stats[model.__tablename__] = total
        return stats

    @staticmethod
    async def set_memory_count(
        session: AsyncSession, end_user_id: uuid.UUID, node_count: int
    ) -> bool:
        result = await session.execute(
            sa.update(EndUser)
            .where(EndUser.id == end_user_id, EndUser.is_active.is_(True))
            .values(memory_count=node_count)
        )
        return (result.rowcount or 0) > 0

    @staticmethod
    async def touch_write_time(
        session: AsyncSession, end_user_id: uuid.UUID
    ) -> bool:
        """Refresh ``end_users.write_time`` for an active target (API parity)."""

        result = await session.execute(
            sa.update(EndUser)
            .where(EndUser.id == end_user_id, EndUser.is_active.is_(True))
            .values(write_time=utcnow_naive())
        )
        return (result.rowcount or 0) > 0

    @staticmethod
    async def existing_ids_any_status(
        session: AsyncSession,
        end_user_ids: set[uuid.UUID],
        workspace_id: uuid.UUID,
    ) -> set[uuid.UUID]:
        """Existing end-user ids in one workspace, regardless of status."""

        if not end_user_ids:
            return set()
        result = await session.execute(
            sa.select(EndUser.id).where(
                EndUser.id.in_(end_user_ids),
                EndUser.workspace_id == workspace_id,
            )
        )
        return set(result.scalars().all())


def select_end_user(end_user_id: uuid.UUID):
    """``EndUser`` by id, ignoring ``is_active`` (pre-soft-delete lookup)."""

    return sa.select(EndUser).where(EndUser.id == end_user_id)


__all__ = ["EndUserMergeRepository", "select_end_user"]
