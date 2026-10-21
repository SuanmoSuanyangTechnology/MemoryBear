"""Workspace access guard: wire-compatible port of the monolith's
``cur_workspace_access_guard`` for the management plane.

The monolith resolves the acting user (JWT) first — missing or inactive user
rows fail with 401 before the guard body runs — then checks the workspace's
existence (404), superuser same-tenant bypass, and ordinary membership (403).
This module keeps that exact evaluation order and the same HTTP detail strings
so the gateway can pass guarded-endpoint errors through unchanged.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..api.dependencies import Principal, get_principal, get_sync_db
from ..repositories import cross_domain_read

logger = logging.getLogger(__name__)


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def ensure_workspace_access(db: Session, principal: Principal) -> UUID:
    """Validate the principal may act inside its current workspace.

    Returns the workspace id after all checks pass; raises the legacy-shaped
    HTTPException otherwise. Runs before quota checks (guards outermost).
    """

    user = cross_domain_read.get_user(db, principal.actor_id)
    if user is None:
        logger.warning("Workspace guard: actor not found: %s", principal.actor_id)
        raise _credentials_exception()
    if not user.is_active:
        logger.warning("Workspace guard: actor disabled: %s", principal.actor_id)
        raise _credentials_exception()

    workspace_id = principal.workspace_id
    if workspace_id is None:
        raise HTTPException(status_code=400, detail="workspace_id is required")

    workspace = cross_domain_read.get_workspace(db, workspace_id)
    if workspace is None:
        logger.warning("Workspace guard: workspace not found: %s", workspace_id)
        raise HTTPException(status_code=404, detail="Workspace not found")

    if user.is_superuser:
        if user.tenant_id == workspace.tenant_id:
            return workspace_id
        logger.warning(
            "Workspace guard: superuser cross-tenant access denied: user=%s",
            principal.actor_id,
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    if not cross_domain_read.is_workspace_member(db, principal.actor_id, workspace_id):
        logger.warning(
            "Workspace guard: non-member access denied: user=%s, workspace=%s",
            principal.actor_id,
            workspace_id,
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    return workspace_id


def require_workspace_access(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_sync_db)],
) -> UUID:
    """FastAPI dependency form of :func:`ensure_workspace_access`."""

    return ensure_workspace_access(db, principal)


__all__ = ["ensure_workspace_access", "require_workspace_access"]
