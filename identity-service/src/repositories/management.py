"""Sync reads for the management plane (SharedBase mirror, host-owned tables).

The async snapshot reader in ``src/repositories/user.py`` stays untouched: the
snapshot path serves the runtime plane, while every function here backs the
migrated management routes and runs on the sync engine.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
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


def get_user_by_email(db: Session, email: str) -> User | None:
    """Any user with this email (host lookup: no active/tenant filter)."""

    return db.execute(select(User).where(User.email == email)).scalars().first()


def create_user_row(
    db: Session,
    *,
    username: str,
    email: str,
    hashed_password: str,
    tenant_id: uuid.UUID,
    is_superuser: bool = False,
) -> User:
    """Insert one ``users`` row and flush; the caller owns commit/refresh."""

    user = User(
        username=username,
        email=email,
        hashed_password=hashed_password,
        tenant_id=tenant_id,
        is_superuser=is_superuser,
    )
    db.add(user)
    db.flush()
    return user


def get_user_by_id_regardless_active(db: Session, user_id: uuid.UUID) -> User | None:
    """User lookup without the active/tenant filters (activate path)."""

    return db.execute(select(User).where(User.id == user_id)).scalars().first()


def check_superuser_only(db: Session) -> bool:
    """True when exactly one active superuser exists (host guard)."""

    count = db.execute(
        select(func.count())
        .select_from(User)
        .where(User.is_active.is_(True), User.is_superuser.is_(True))
    ).scalar_one()
    return count == 1


def get_superusers_by_tenant(
    db: Session, tenant_id: uuid.UUID, is_active: bool | None = True
) -> list[User]:
    """Tenant superusers; ``is_active=None`` disables the active filter."""

    stmt = select(User).where(
        User.tenant_id == tenant_id, User.is_superuser.is_(True)
    )
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    return list(db.execute(stmt).scalars().all())


def get_superuser(db: Session) -> User | None:
    """First active superuser (host setup guard)."""

    return (
        db.execute(
            select(User).where(
                User.is_active.is_(True), User.is_superuser.is_(True)
            )
        )
        .scalars()
        .first()
    )


def get_tenant_by_name(db: Session, name: str) -> Tenants | None:
    """Exact-name tenant lookup (setup bootstrap duplicate guard)."""

    return db.execute(select(Tenants).where(Tenants.name == name)).scalars().first()


def create_tenant_row(
    db: Session, *, name: str, description: str | None
) -> Tenants:
    """Insert one ``tenants`` row and flush (setup bootstrap only, decision D6)."""

    tenant = Tenants(
        id=uuid.uuid4(), name=name, description=description, is_active=True
    )
    db.add(tenant)
    db.flush()
    return tenant
