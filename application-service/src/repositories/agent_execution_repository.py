"""Agent-execution read paths used by the application-log endpoints (frozen
port of the monolith ``agent_execution_repository`` read subset)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.agent_execution_model import AgentExecution

logger = logging.getLogger(__name__)


class AgentExecutionRepository:
    """Agent execution record reads (app-log face)."""

    def __init__(self, db: Session):
        self.db = db

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
