"""App table read paths (frozen port of the monolith's app_repository reads)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.app_model import App


class AppRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_apps_by_id(self, app_id: UUID) -> App | None:
        return self.db.query(App).filter(App.id == app_id, App.is_active.is_(True)).first()

    def get_apps_by_name(self, app_name: str, app_type: str, workspace_id: UUID) -> list[App]:
        stmt = select(App).where(
            App.name == app_name,
            App.workspace_id == workspace_id,
            App.type == app_type,
            App.is_active.is_(True),
        )
        return list(self.db.execute(stmt).scalars().all())


def get_apps_by_id(db: Session, app_id: UUID) -> App | None:
    """Get one active app by id (host backward-compatible function form)."""

    return AppRepository(db).get_apps_by_id(app_id)


__all__ = ["AppRepository", "get_apps_by_id"]
