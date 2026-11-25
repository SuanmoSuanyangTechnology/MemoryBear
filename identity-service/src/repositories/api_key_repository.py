"""API-key repository (sync management plane).

Port of ``core/api/app/repositories/api_key_repository.py`` limited to the
paths the management routes need. ``update`` keeps the host's None allow-list
and ``delete`` is a soft delete (``is_active=False``).
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.core.datetime_utils import utcnow_naive
from src.models.api_key_model import ApiKey, ApiKeyLog
from src.models.app_mirror_model import App
from src.schemas.api_key_schema import ApiKeyQuery

_UPDATE_NONE_ALLOWED = frozenset({"description", "quota_limit", "expires_at"})


def create(db: Session, api_key_data: dict[str, Any]) -> ApiKey:
    api_key = ApiKey(**api_key_data)
    db.add(api_key)
    db.flush()
    return api_key


def get_by_id(db: Session, api_key_id: uuid.UUID) -> ApiKey | None:
    return db.get(ApiKey, api_key_id)


def get_app_by_id(db: Session, app_id: uuid.UUID) -> App | None:
    """Read the apps mirror for the resource and publish gates."""

    return db.get(App, app_id)


def get_active_by_name(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    resource_id: uuid.UUID | None,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> ApiKey | None:
    stmt = sa.select(ApiKey).where(
        ApiKey.workspace_id == workspace_id,
        ApiKey.resource_id == resource_id,
        ApiKey.name == name,
        ApiKey.is_active.is_(True),
    )
    if exclude_id is not None:
        stmt = stmt.where(ApiKey.id != exclude_id)
    return db.scalars(stmt).first()


def list_by_workspace(
    db: Session, workspace_id: uuid.UUID, query: ApiKeyQuery
) -> tuple[list[ApiKey], int]:
    stmt = sa.select(ApiKey).where(ApiKey.workspace_id == workspace_id)

    if query.type:
        stmt = stmt.where(ApiKey.type == query.type)
    if query.is_active is not None:
        stmt = stmt.where(ApiKey.is_active == query.is_active)
    if query.resource_id:
        stmt = stmt.where(ApiKey.resource_id == query.resource_id)

    total = db.execute(
        sa.select(sa.func.count()).select_from(stmt.subquery())
    ).scalar()

    stmt = stmt.order_by(ApiKey.created_at.desc())
    stmt = stmt.offset((query.page - 1) * query.pagesize).limit(query.pagesize)
    items = db.scalars(stmt).all()
    return list(items), int(total or 0)


def update(
    db: Session, api_key_id: uuid.UUID, update_data: dict[str, Any]
) -> ApiKey | None:
    api_key = db.get(ApiKey, api_key_id)
    if api_key:
        for key, value in update_data.items():
            if key in _UPDATE_NONE_ALLOWED:
                setattr(api_key, key, value)
            elif value is not None:
                setattr(api_key, key, value)
        db.flush()
    return api_key


def delete(db: Session, api_key_id: uuid.UUID) -> bool:
    api_key = db.get(ApiKey, api_key_id)
    if api_key:
        api_key.is_active = False
        db.flush()
        return True
    return False


def get_stats(db: Session, api_key_id: uuid.UUID) -> dict[str, Any]:
    api_key = db.get(ApiKey, api_key_id)
    if not api_key:
        return {}

    today_start = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    requests_today = (
        db.execute(
            sa.select(sa.func.count())
            .select_from(ApiKeyLog)
            .where(
                sa.and_(
                    ApiKeyLog.api_key_id == api_key_id,
                    ApiKeyLog.created_at >= today_start,
                )
            )
        ).scalar()
        or 0
    )

    avg_response_time = db.execute(
        sa.select(sa.func.avg(ApiKeyLog.response_time)).where(
            ApiKeyLog.api_key_id == api_key_id
        )
    ).scalar()

    return {
        "total_requests": api_key.usage_count,
        "requests_today": requests_today,
        "quota_used": api_key.quota_used,
        "quota_limit": api_key.quota_limit,
        "last_used_at": api_key.last_used_at,
        "rate_limit": api_key.rate_limit,
        "avg_response_time": (
            float(avg_response_time) if avg_response_time else None
        ),
    }


def list_logs_by_api_key(
    db: Session,
    api_key_id: uuid.UUID,
    filters: dict[str, Any],
    page: int,
    pagesize: int,
) -> tuple[list[ApiKeyLog], int]:
    stmt = sa.select(ApiKeyLog).where(ApiKeyLog.api_key_id == api_key_id)

    if filters.get("start_date"):
        stmt = stmt.where(ApiKeyLog.created_at >= filters["start_date"])
    if filters.get("end_date"):
        stmt = stmt.where(ApiKeyLog.created_at <= filters["end_date"])
    if filters.get("status_code"):
        stmt = stmt.where(ApiKeyLog.status_code == filters["status_code"])
    if filters.get("endpoint"):
        stmt = stmt.where(ApiKeyLog.endpoint.ilike(f"%{filters['endpoint']}%"))

    total = db.execute(
        sa.select(sa.func.count()).select_from(stmt.subquery())
    ).scalar()

    stmt = stmt.order_by(ApiKeyLog.created_at.desc())
    stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)
    items = db.scalars(stmt).all()
    return list(items), int(total or 0)
