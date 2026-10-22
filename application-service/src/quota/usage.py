"""Quota admission checks for the application domain (management plane).

Port of the monolith's ``_check_quota`` plus the ``check_app_quota`` /
``check_skill_quota`` decorator bodies. In the monolith the decorators read
``db`` / ``user`` / ``workspace_id`` out of the wrapped call's kwargs; here the
FastAPI dependency graph supplies ``db`` and the request principal, so the
check bodies take them as plain arguments. Guard runs first (outermost), so a
missing workspace id only reaches the app check through a programming error.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..api.dependencies import Principal, get_principal, get_sync_db
from ..errors import InternalServerError, QuotaExceededError
from ..guards.workspace_access import require_workspace_access
from ..models.app_model import App
from ..models.end_user_model import EndUser
from ..models.skill_model import Skill
from ..repositories import cross_domain_read
from .config import get_quota_config

logger = logging.getLogger(__name__)


def count_active_apps(db: Session, workspace_id: UUID) -> int:
    return (
        db.query(App)
        .filter(App.workspace_id == workspace_id, App.is_active.is_(True))
        .count()
    )


def count_active_skills(db: Session, tenant_id: UUID) -> int:
    return (
        db.query(Skill)
        .filter(Skill.tenant_id == tenant_id, Skill.is_active.is_(True))
        .count()
    )


def count_active_end_users(
    db: Session, tenant_id: UUID, workspace_id: UUID | None = None
) -> int:
    """Host ``QuotaUsageRepository.count_end_users``: active end users under the
    workspace (or the tenant), trial users (``other_id`` matching a tenant
    user) excluded."""

    query = db.query(EndUser).filter(EndUser.is_active.is_(True))
    if workspace_id:
        query = query.filter(EndUser.workspace_id == workspace_id)
    else:
        workspace_ids = cross_domain_read.get_workspace_ids_by_tenant(db, tenant_id)
        if not workspace_ids:
            return 0
        query = query.filter(EndUser.workspace_id.in_(workspace_ids))

    trial_user_ids = [
        str(user_id) for user_id in cross_domain_read.get_tenant_user_ids(db, tenant_id)
    ]
    if trial_user_ids:
        query = query.filter(~EndUser.other_id.in_(trial_user_ids))
    return query.count()


def _dispatch_usage(
    db: Session, tenant_id: UUID, quota_type: str, workspace_id: UUID | None
) -> int | float:
    if quota_type == "app_quota":
        return count_active_apps(db, workspace_id) if workspace_id else 0
    if quota_type == "skill_quota":
        return count_active_skills(db, tenant_id)
    if quota_type == "end_user_quota":
        return count_active_end_users(db, tenant_id, workspace_id)
    # Unknown quota types resolve to zero usage, matching the host dispatch
    # fallback for quota types this service does not meter.
    return 0


def check_quota(
    db: Session,
    tenant_id: UUID,
    quota_type: str,
    resource_name: str,
    usage_func: Callable[..., int | float] | None = None,
    workspace_id: UUID | None = None,
) -> None:
    """Compare current usage against the limit and raise 402 when exhausted."""

    try:
        quota_config = get_quota_config(db, tenant_id)
        if not quota_config:
            logger.warning("Tenant %s has no quota config; skipping check", tenant_id)
            return

        quota_limit = quota_config.get(quota_type)
        if quota_limit is None:
            logger.warning("Quota config lacks %s; skipping check", quota_type)
            return

        if usage_func:
            current_usage = (
                usage_func(db, tenant_id, workspace_id)
                if workspace_id
                else usage_func(db, tenant_id)
            )
        else:
            current_usage = _dispatch_usage(db, tenant_id, quota_type, workspace_id)

        if current_usage >= quota_limit:
            logger.warning(
                "Quota exceeded: tenant=%s, workspace=%s, type=%s, usage=%s, limit=%s",
                tenant_id,
                workspace_id,
                quota_type,
                current_usage,
                quota_limit,
            )
            raise QuotaExceededError(
                resource=resource_name,
                current_usage=current_usage,
                quota_limit=quota_limit,
            )

    except QuotaExceededError:
        raise
    except Exception as error:
        logger.error(
            "Quota check failed: tenant=%s, workspace=%s, type=%s, error_type=%s, error=%s",
            tenant_id,
            workspace_id,
            quota_type,
            type(error).__name__,
            error,
            exc_info=True,
        )
        raise


def ensure_app_quota(db: Session, principal: Principal) -> None:
    """Workspace-scoped app-count check (create/copy/import paths)."""

    workspace_id = principal.workspace_id
    if workspace_id is None:
        logger.error("check_app_quota: workspace_id missing; refusing request")
        raise InternalServerError()
    check_quota(
        db,
        principal.tenant_id,
        "app_quota",
        "app",
        workspace_id=workspace_id,
    )


def ensure_skill_quota(db: Session, principal: Principal) -> None:
    """Tenant-scoped skill-count check."""

    check_quota(db, principal.tenant_id, "skill_quota", "skill")


def ensure_end_user_quota(db: Session, principal: Principal) -> None:
    """Workspace-scoped end-user-count check (end-user create path)."""

    workspace_id = principal.workspace_id
    if workspace_id is None:
        logger.error("check_end_user_quota: workspace_id missing; refusing request")
        raise InternalServerError()
    check_quota(
        db,
        principal.tenant_id,
        "end_user_quota",
        "end_user",
        workspace_id=workspace_id,
    )


async def check_end_user_quota_async(
    db: AsyncSession,
    tenant_id: UUID,
    workspace_id: UUID | None = None,
) -> None:
    """Async end-user quota admission for the runtime plane.

    Bridges to the sync check through ``run_sync`` so config resolution and
    counting (trial users excluded) share one implementation.
    """

    await db.run_sync(
        lambda sync_db: check_quota(
            sync_db,
            tenant_id,
            "end_user_quota",
            "end_user",
            workspace_id=workspace_id,
        )
    )


def require_app_quota(
    _: Annotated[UUID, Depends(require_workspace_access)],
    db: Annotated[Session, Depends(get_sync_db)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """FastAPI dependency form of the host ``check_app_quota`` decorator.

    The workspace guard is an explicit sub-dependency so the monolith's check
    order (guard outermost, quota second) holds regardless of how the route
    lists its dependencies.
    """

    ensure_app_quota(db, principal)


def require_skill_quota(
    db: Annotated[Session, Depends(get_sync_db)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """FastAPI dependency form of the host ``check_skill_quota`` decorator.

    The host skill routes carry no workspace guard (auth + quota only), so the
    tenant-scoped skill check runs without requiring a workspace context.
    """

    ensure_skill_quota(db, principal)


def require_end_user_quota(
    db: Annotated[Session, Depends(get_sync_db)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """FastAPI dependency form of the host ``check_end_user_quota`` decorator.

    The host end-user create route carries no workspace guard either (the API
    key pins the workspace); a missing workspace fails as a server error, the
    same posture as the host wrapper.
    """

    ensure_end_user_quota(db, principal)


__all__ = [
    "check_end_user_quota_async",
    "check_quota",
    "count_active_apps",
    "count_active_end_users",
    "count_active_skills",
    "ensure_app_quota",
    "ensure_end_user_quota",
    "ensure_skill_quota",
    "require_app_quota",
    "require_end_user_quota",
    "require_skill_quota",
]
