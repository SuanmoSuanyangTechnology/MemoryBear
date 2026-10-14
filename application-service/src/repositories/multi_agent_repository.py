"""Multi-agent config table read paths (mirrors the monolith's
``MultiAgentService.get_config`` read, shared by the publish flow)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.multi_agent_model import MultiAgentConfig


class MultiAgentConfigRepository:
    """Multi-agent config repository"""

    def __init__(self, db: Session):
        self.db = db

    def get_by_app_id(self, app_id: UUID) -> MultiAgentConfig | None:
        """The app's current active multi-agent config (latest row)."""

        return self.db.scalars(
            select(MultiAgentConfig)
            .where(
                MultiAgentConfig.app_id == app_id,
                MultiAgentConfig.is_active.is_(True),
            )
            .order_by(MultiAgentConfig.updated_at.desc())
        ).first()


__all__ = ["MultiAgentConfigRepository"]
