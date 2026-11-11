"""Read-only queries for the legacy short-term aggregation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from src.models.references.end_user_model import EndUser
from src.models.references.memory_statistics_model import (
    Conversation,
    MemoryMessage,
    ShortTermMemory,
)

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime


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
