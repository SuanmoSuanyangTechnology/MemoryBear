"""Prompt-optimizer data access (port of the host ``prompt_optimizer_repository``).

Sessions and their history carry both the sync management-plane methods and
the async twins used by the runtime-plane streaming path; releases are
management-plane only. The release list batches the first-user-message lookup
into one query (host semantics without the per-row N+1).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.prompt_optimizer_model import (
    PromptHistory,
    PromptOptimizerSession,
    PromptOptimizerSessionHistory,
    RoleType,
)


class PromptOptimizerSessionRepository:
    """Repository for managing prompt optimization sessions and session history."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def get_session_by_id(self, session_id: UUID) -> PromptOptimizerSession | None:
        return (
            self.db.query(PromptOptimizerSession)
            .filter(PromptOptimizerSession.id == session_id)
            .first()
        )

    def create_session(self, tenant_id: UUID, user_id: UUID) -> PromptOptimizerSession:
        session = PromptOptimizerSession(tenant_id=tenant_id, user_id=user_id)
        self.db.add(session)
        return session

    def get_session_history(
        self, session_id: UUID, user_id: UUID
    ) -> list[PromptOptimizerSessionHistory]:
        """Chronological messages of a session owned by the user.

        A missing (or foreign) session reads as an empty history — this read
        path has no not-found error (host semantics).
        """

        session = (
            self.db.query(PromptOptimizerSession)
            .filter(
                PromptOptimizerSession.id == session_id,
                PromptOptimizerSession.user_id == user_id,
            )
            .first()
        )
        if not session:
            return []

        return (
            self.db.query(PromptOptimizerSessionHistory)
            .filter(
                PromptOptimizerSessionHistory.session_id == session.id,
                PromptOptimizerSessionHistory.user_id == user_id,
            )
            .order_by(PromptOptimizerSessionHistory.created_at.asc())
            .all()
        )

    async def get_session_history_async(
        self, session_id: UUID, user_id: UUID
    ) -> list[PromptOptimizerSessionHistory]:
        """Async twin for the runtime plane; ``self.db`` must be an AsyncSession."""

        session = await self.db.scalar(
            select(PromptOptimizerSession).filter(
                PromptOptimizerSession.id == session_id,
                PromptOptimizerSession.user_id == user_id,
            )
        )
        if not session:
            return []

        result = await self.db.execute(
            select(PromptOptimizerSessionHistory)
            .filter(
                PromptOptimizerSessionHistory.session_id == session.id,
                PromptOptimizerSessionHistory.user_id == user_id,
            )
            .order_by(PromptOptimizerSessionHistory.created_at.asc())
        )
        return list(result.scalars().all())

    async def create_message_async(
        self,
        tenant_id: UUID,
        session_id: UUID,
        user_id: UUID,
        role: RoleType,
        content: str,
    ) -> PromptOptimizerSessionHistory:
        """Async twin for the runtime plane; ``self.db`` must be an AsyncSession."""

        session = await self.db.scalar(
            select(PromptOptimizerSession).filter(
                PromptOptimizerSession.id == session_id,
                PromptOptimizerSession.user_id == user_id,
                PromptOptimizerSession.tenant_id == tenant_id,
            )
        )
        if not session:
            raise ValueError(f"Session {session_id} not found for user {user_id}")

        message = PromptOptimizerSessionHistory(
            tenant_id=tenant_id,
            session_id=session.id,
            user_id=user_id,
            role=role.value,
            content=content,
        )
        self.db.add(message)
        return message

    def get_first_user_messages(self, session_ids: list[UUID]) -> dict[UUID, str]:
        """First user message per session (batched, created_at ascending)."""

        if not session_ids:
            return {}

        rows = (
            self.db.query(PromptOptimizerSessionHistory)
            .filter(
                PromptOptimizerSessionHistory.session_id.in_(session_ids),
                PromptOptimizerSessionHistory.role == RoleType.USER.value,
            )
            .order_by(
                PromptOptimizerSessionHistory.session_id,
                PromptOptimizerSessionHistory.created_at.asc(),
            )
            .all()
        )

        first_messages: dict[UUID, str] = {}
        for row in rows:
            first_messages.setdefault(row.session_id, row.content)
        return first_messages


class PromptReleaseRepository:
    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def get_prompt_by_session_id(self, session_id: UUID) -> PromptHistory | None:
        return (
            self.db.query(PromptHistory)
            .filter(
                PromptHistory.session_id == session_id,
                PromptHistory.is_delete.is_(False),
            )
            .first()
        )

    def create_prompt_release(
        self,
        tenant_id: UUID,
        title: str,
        session_id: UUID,
        prompt: str,
    ) -> PromptHistory:
        prompt_obj = PromptHistory(
            tenant_id=tenant_id,
            title=title,
            session_id=session_id,
            prompt=prompt,
        )
        self.db.add(prompt_obj)
        return prompt_obj

    def soft_delete_prompt(self, prompt_obj: PromptHistory) -> None:
        prompt_obj.is_delete = True

    def get_prompt_by_id(self, prompt_id: UUID) -> PromptHistory | None:
        return (
            self.db.query(PromptHistory).filter(PromptHistory.id == prompt_id).first()
        )

    def count_prompts(self, tenant_id: UUID) -> int:
        return (
            self.db.query(PromptHistory)
            .filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False),
            )
            .count()
        )

    def get_prompts_paginated(
        self, tenant_id: UUID, offset: int, limit: int
    ) -> list[PromptHistory]:
        return (
            self.db.query(PromptHistory)
            .filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False),
            )
            .order_by(PromptHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count_prompts_by_keyword(self, tenant_id: UUID, keyword: str) -> int:
        return (
            self.db.query(PromptHistory)
            .filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False),
                PromptHistory.title.ilike(f"%{keyword}%"),
            )
            .count()
        )

    def search_prompts_paginated(
        self, tenant_id: UUID, keyword: str, offset: int, limit: int
    ) -> list[PromptHistory]:
        return (
            self.db.query(PromptHistory)
            .filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False),
                PromptHistory.title.ilike(f"%{keyword}%"),
            )
            .order_by(PromptHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )


__all__ = ["PromptOptimizerSessionRepository", "PromptReleaseRepository"]
