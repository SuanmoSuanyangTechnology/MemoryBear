"""Agent-execution read paths used by the application-log endpoints plus the
chat-kernel write path (frozen port of the monolith
``agent_execution_repository`` subset)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.agent_execution_model import AgentExecution
from ..utils.datetime_utils import utcnow_naive

logger = logging.getLogger(__name__)


class AgentExecutionRepository:
    """Agent execution record reads and completion writes."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def create(self, execution: AgentExecution) -> AgentExecution:
        """Stage a new execution record; the caller owns the commit."""

        self.db.add(execution)
        self.db.flush()
        return execution

    async def create_async(self, execution: AgentExecution) -> AgentExecution:
        """Stage a new execution record on the async session."""

        self.db.add(execution)
        await self.db.flush()
        return execution

    def update_completed(
        self,
        execution_id: uuid.UUID,
        *,
        steps: list,
        status: str = "completed",
        elapsed_time: float | None = None,
        token_usage: dict | None = None,
        error_message: str | None = None,
        completed_at: Any = None,
        message_id: uuid.UUID | None = None,
        agent_log: dict | None = None,
    ) -> None:
        """Mark an execution completed (or failed) and commit."""

        updates = {
            "steps": steps,
            "status": status,
            "completed_at": completed_at or utcnow_naive(),
        }
        if elapsed_time is not None:
            updates["elapsed_time"] = elapsed_time
        if token_usage is not None:
            updates["token_usage"] = token_usage
        if error_message is not None:
            updates["error_message"] = error_message
        if message_id is not None:
            updates["message_id"] = message_id
        if agent_log is not None:
            updates["agent_log"] = agent_log

        record = self.db.scalars(
            select(AgentExecution).where(AgentExecution.id == execution_id)
        ).first()
        if record:
            for key, value in updates.items():
                setattr(record, key, value)
            self.db.commit()

    async def update_completed_async(
        self,
        execution_id: uuid.UUID,
        *,
        steps: list,
        status: str = "completed",
        elapsed_time: float | None = None,
        token_usage: dict | None = None,
        error_message: str | None = None,
        completed_at: Any = None,
        message_id: uuid.UUID | None = None,
        agent_log: dict | None = None,
    ) -> None:
        """Async variant of ``update_completed``."""

        updates = {
            "steps": steps,
            "status": status,
            "completed_at": completed_at or utcnow_naive(),
        }
        if elapsed_time is not None:
            updates["elapsed_time"] = elapsed_time
        if token_usage is not None:
            updates["token_usage"] = token_usage
        if error_message is not None:
            updates["error_message"] = error_message
        if message_id is not None:
            updates["message_id"] = message_id
        if agent_log is not None:
            updates["agent_log"] = agent_log

        result = await self.db.execute(
            select(AgentExecution).where(AgentExecution.id == execution_id)
        )
        record = result.scalar_one_or_none()
        if record:
            for key, value in updates.items():
                setattr(record, key, value)
            await self.db.commit()

    def get_by_conversation(
        self,
        conversation_id: uuid.UUID,
        agent_role: str | None = None,
    ) -> list[AgentExecution]:
        """Executions of one conversation in ascending time order.

        ``agent_role="master"`` is mandatory for multi-agent log views: without
        the filter, sub-agent records would be treated as standalone executions
        and stick to unrelated messages.
        """

        stmt = select(AgentExecution).where(
            AgentExecution.conversation_id == conversation_id
        )
        if agent_role is not None:
            stmt = stmt.where(AgentExecution.agent_role == agent_role)
        stmt = stmt.order_by(AgentExecution.started_at.asc())
        return list(self.db.scalars(stmt).all())

    def get_page_scoped(
        self,
        conversation_id: uuid.UUID,
        *,
        message_ids: set[uuid.UUID],
        window_start,
        window_end,
        agent_role: str,
    ) -> list[AgentExecution]:
        """Executions relevant to one page of messages.

        Rows are kept when they either link directly to a page message
        (``message_id``) or fall inside the page's time window (covers legacy
        rows predating the message_id backfill, which the log view matches by
        nearest time). Bounded on both dimensions, so a long conversation's
        full execution history is never loaded for one page.
        """

        if not message_ids and window_start is None:
            return []

        stmt = select(AgentExecution).where(
            AgentExecution.conversation_id == conversation_id,
            AgentExecution.agent_role == agent_role,
        )
        scope_clauses = []
        if message_ids:
            scope_clauses.append(AgentExecution.message_id.in_(message_ids))
        if window_start is not None:
            window_clause = AgentExecution.started_at >= window_start
            if window_end is not None:
                window_clause = window_clause & (AgentExecution.started_at <= window_end)
            scope_clauses.append(window_clause)
        stmt = stmt.where(or_(*scope_clauses))
        stmt = stmt.order_by(AgentExecution.started_at.asc())
        return list(self.db.scalars(stmt).all())

    def list_sub_by_parents(
        self,
        conversation_id: uuid.UUID,
        parent_ids: set[uuid.UUID],
    ) -> list[AgentExecution]:
        """Sub-agent executions of the given parent ids in one query (the
        paged counterpart of ``list_sub_by_conversation``)."""

        if not parent_ids:
            return []
        stmt = (
            select(AgentExecution)
            .where(
                AgentExecution.conversation_id == conversation_id,
                AgentExecution.agent_role == "sub",
                AgentExecution.parent_execution_id.in_(parent_ids),
            )
            .order_by(AgentExecution.started_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_message_id(
        self,
        message_id: uuid.UUID,
        agent_role: str | None = None,
    ) -> AgentExecution | None:
        """Execution record of one assistant message.

        ``agent_role="master"`` is passed by the message-detail view so sub-agent
        rows never shadow the master execution.
        """

        stmt = select(AgentExecution).where(AgentExecution.message_id == message_id)
        if agent_role is not None:
            stmt = stmt.where(AgentExecution.agent_role == agent_role)
        return self.db.scalars(stmt).first()

    def list_by_parent(self, parent_execution_id: uuid.UUID) -> list[AgentExecution]:
        """Sub-agent executions of one parent execution in ascending time order
        (one row per activation)."""

        stmt = (
            select(AgentExecution)
            .where(AgentExecution.parent_execution_id == parent_execution_id)
            .order_by(AgentExecution.started_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_sub_by_conversation(self, conversation_id: uuid.UUID) -> list[AgentExecution]:
        """All sub-agent executions of a conversation in one query (avoids
        per-master N+1 on the detail page)."""

        stmt = (
            select(AgentExecution)
            .where(
                AgentExecution.conversation_id == conversation_id,
                AgentExecution.agent_role == "sub",
            )
            .order_by(AgentExecution.started_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def count_sub_by_conversations(self, conversation_ids: list[uuid.UUID]) -> dict[str, int]:
        """Sub-agent execution count per conversation (list page column).

        Single GROUP BY query; callers degrade any exception to 0 without
        blocking the log list.
        """

        if not conversation_ids:
            return {}
        stmt = (
            select(
                AgentExecution.conversation_id,
                func.count(AgentExecution.id),
            )
            .where(
                AgentExecution.conversation_id.in_(conversation_ids),
                AgentExecution.agent_role == "sub",
            )
            .group_by(AgentExecution.conversation_id)
        )
        rows = self.db.execute(stmt).all()
        return {str(row[0]): int(row[1]) for row in rows}


__all__ = ["AgentExecutionRepository"]
