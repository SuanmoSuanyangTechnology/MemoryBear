"""Sync reads for the management plane (SharedBase mirror, host-owned tables).

The async snapshot reader in ``src/repositories/user.py`` stays untouched: the
snapshot path serves the runtime plane, while every function here backs the
migrated management routes and runs on the sync engine.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.tenant_model import Tenants
from src.models.user_model import User
from src.models.workspace_model import Workspace, WorkspaceMember


def get_user_by_id(db: Session, user_id: uuid.UUID | str) -> User | None:
    """Active user whose tenant is active (host ``get_user_by_id`` semantics)."""

    user = (
        db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
        .scalars()
        .first()
    )
    if user is None:
        return None
    tenant = db.get(Tenants, user.tenant_id)
    if tenant is not None and not tenant.is_active:
        return None
    return user


def get_tenant_by_id(db: Session, tenant_id: uuid.UUID) -> Tenants | None:
    return db.get(Tenants, tenant_id)


def get_workspace_by_id(db: Session, workspace_id: uuid.UUID) -> Workspace | None:
    return db.get(Workspace, workspace_id)


def get_member_in_workspace(
    db: Session, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> WorkspaceMember | None:
    return (
        db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.is_active.is_(True),
            )
        )
        .scalars()
        .first()
    )
