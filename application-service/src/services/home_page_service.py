"""Home-page BFF service (port of the monolith ``HomePageService``).

Statistics and workspace cards are computed locally: ``apps``/``end_users``
are owned tables and ``workspaces``/``model_configs`` ride the sanctioned
cross-domain read exit, so no per-request hop to identity/model-service is
needed. The version card is owned by ops-service (``version_notes``) and is
read east-west; when that read fails the card degrades to ``SYSTEM_VERSION``
with empty structured intros — the monolith's own last-resort fallback.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from ..config import current_settings
from ..integrations.ops_service import OpsServiceClient
from ..repositories.home_page_repository import HomePageRepository
from ..schemas.home_page_schema import HomeStatistics, WorkspaceInfo
from ..utils.datetime_utils import utcnow_naive

_EMPTY_VERSION_INTRODUCTION = {
    "codeName": "",
    "releaseDate": "",
    "upgradePosition": "",
    "coreUpgrades": [],
}


def _week_start() -> datetime:
    now = utcnow_naive()
    start = now - timedelta(days=now.weekday())
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def _week_growth_rate(total: int, new_this_week: int) -> float:
    if total == 0 or new_this_week == 0:
        return 0.0
    last_week_total = total - new_this_week
    if last_week_total == 0:
        return 100.0
    return round((new_this_week / last_week_total) * 100, 2)


class HomePageService:
    def __init__(self, db: Session):
        self._repository = HomePageRepository(db)

    def get_home_statistics(self, tenant_id: UUID) -> HomeStatistics:
        week_start = _week_start()

        (
            total_models,
            total_llm,
            total_embedding,
            new_models_this_week,
        ) = self._repository.count_models(tenant_id, week_start)
        (
            active_workspaces,
            new_workspaces_this_week,
        ) = self._repository.count_workspaces(tenant_id, week_start)
        total_users, new_users_this_week = self._repository.count_users(
            tenant_id, week_start
        )
        running_apps, new_apps_this_week = self._repository.count_apps(
            tenant_id, week_start
        )

        return HomeStatistics(
            total_models=total_models,
            total_llm=total_llm,
            total_embedding=total_embedding,
            model_week_growth_rate=_week_growth_rate(
                total_models, new_models_this_week
            ),
            active_workspaces=active_workspaces,
            new_workspaces_this_week=new_workspaces_this_week,
            workspace_week_growth_rate=_week_growth_rate(
                active_workspaces, new_workspaces_this_week
            ),
            total_users=total_users,
            new_users_this_week=new_users_this_week,
            user_week_growth_rate=_week_growth_rate(total_users, new_users_this_week),
            running_apps=running_apps,
            new_apps_this_week=new_apps_this_week,
            app_week_growth_rate=_week_growth_rate(running_apps, new_apps_this_week),
        )

    def get_workspace_list(self, tenant_id: UUID) -> list[WorkspaceInfo]:
        (
            workspaces,
            app_count_dict,
            user_count_dict,
        ) = self._repository.list_workspaces_with_counts(tenant_id)

        return [
            WorkspaceInfo(
                id=str(workspace.id),
                name=workspace.name,
                icon=workspace.icon,
                description=workspace.description,
                app_count=app_count_dict.get(workspace.id, 0),
                user_count=user_count_dict.get(workspace.id, 0),
                created_at=workspace.created_at,
            )
            for workspace in workspaces
        ]

    @staticmethod
    def get_system_version(client: OpsServiceClient) -> dict[str, Any]:
        payload = client.get_home_page_version()
        if payload:
            return payload
        return {
            "version": current_settings().system_version,
            "introduction": dict(_EMPTY_VERSION_INTRODUCTION),
            "introduction_en": dict(_EMPTY_VERSION_INTRODUCTION),
        }


__all__ = ["HomePageService"]
