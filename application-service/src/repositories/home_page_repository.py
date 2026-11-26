"""Home-page aggregate reads (queries mirror the monolith repository).

``apps`` / ``end_users`` are ServiceBase-owned tables and are queried
directly. ``workspaces`` / ``model_configs`` are host-owned and ride the
sanctioned cross-domain read exit (the same ORM mirrors guards and
application statistics already use — read-only, never DDL).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.app_model import App
from ..models.end_user_model import EndUser
from ..models.models_model import ModelConfig
from ..models.workspace_model import Workspace


class HomePageRepository:
    def __init__(self, db: Session):
        self.db = db

    def count_models(
        self, tenant_id: UUID, week_start: datetime
    ) -> tuple[int, int, int, int]:
        """(total, llm, embedding, new this week) active model configs."""

        base = self.db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active.is_(True),
        )
        total = base.count()
        total_llm = base.filter(ModelConfig.type == "llm").count()
        total_embedding = base.filter(ModelConfig.type == "embedding").count()
        new_this_week = base.filter(ModelConfig.created_at >= week_start).count()
        return total, total_llm, total_embedding, new_this_week

    def count_workspaces(
        self, tenant_id: UUID, week_start: datetime
    ) -> tuple[int, int]:
        """(active, new this week) workspaces of the tenant."""

        base = self.db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True),
        )
        active = base.count()
        new_this_week = base.filter(Workspace.created_at >= week_start).count()
        return active, new_this_week

    def count_users(
        self, tenant_id: UUID, week_start: datetime
    ) -> tuple[int, int]:
        """(total, new this week) end users of active apps in active workspaces."""

        base = (
            self.db.query(EndUser)
            .join(App, EndUser.app_id == App.id)
            .join(Workspace, Workspace.id == App.workspace_id)
            .filter(
                Workspace.tenant_id == tenant_id,
                Workspace.is_active.is_(True),
                App.is_active.is_(True),
                App.status == "active",
                EndUser.is_active.is_(True),
            )
        )
        total = base.count()
        new_this_week = base.filter(EndUser.created_at >= week_start).count()
        return total, new_this_week

    def count_apps(
        self, tenant_id: UUID, week_start: datetime
    ) -> tuple[int, int]:
        """(running, new this week) active apps in active workspaces."""

        base = (
            self.db.query(App)
            .join(Workspace, Workspace.id == App.workspace_id)
            .filter(
                Workspace.tenant_id == tenant_id,
                Workspace.is_active.is_(True),
                App.is_active.is_(True),
                App.status == "active",
            )
        )
        running = base.count()
        new_this_week = base.filter(App.created_at >= week_start).count()
        return running, new_this_week

    def list_workspaces_with_counts(
        self, tenant_id: UUID
    ) -> tuple[list[Workspace], dict[UUID, int], dict[UUID, int]]:
        """Active workspaces plus per-workspace app/user counts."""

        workspaces = (
            self.db.query(Workspace)
            .filter(
                Workspace.tenant_id == tenant_id,
                Workspace.is_active.is_(True),
            )
            .all()
        )
        workspace_ids = [workspace.id for workspace in workspaces]

        app_counts = (
            self.db.query(App.workspace_id, func.count(App.id))
            .filter(
                App.workspace_id.in_(workspace_ids),
                App.is_active.is_(True),
                App.status == "active",
            )
            .group_by(App.workspace_id)
            .all()
        )
        app_count_dict = {workspace_id: count for workspace_id, count in app_counts}

        user_counts = (
            self.db.query(App.workspace_id, func.count(EndUser.id))
            .join(EndUser, EndUser.app_id == App.id)
            .filter(
                App.workspace_id.in_(workspace_ids),
                App.is_active.is_(True),
                App.status == "active",
                EndUser.is_active.is_(True),
            )
            .group_by(App.workspace_id)
            .all()
        )
        user_count_dict = {workspace_id: count for workspace_id, count in user_counts}

        return workspaces, app_count_dict, user_count_dict


__all__ = ["HomePageRepository"]
