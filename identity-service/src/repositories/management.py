"""Sync reads for the management plane (SharedBase mirror, host-owned tables).

The async snapshot reader in ``src/repositories/user.py`` stays untouched: the
snapshot path serves the runtime plane, while every function here backs the
migrated management routes and runs on the sync engine.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
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
    preferred_language: str = "zh",
    external_id: str | None = None,
    external_source: str | None = None,
    current_workspace_id: uuid.UUID | None = None,
) -> User:
    """Insert one ``users`` row and flush; the caller owns commit/refresh."""

    user = User(
        username=username,
        email=email,
        hashed_password=hashed_password,
        tenant_id=tenant_id,
        is_superuser=is_superuser,
        preferred_language=preferred_language,
        external_id=external_id,
        external_source=external_source,
        current_workspace_id=current_workspace_id,
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


# ---- Tenant-admin provisioning (P2-W3: identity owns every users write) ----


def get_tenant_admins(db: Session, tenant_id: uuid.UUID) -> list[User]:
    """Tenant superusers in creation order (ops admin list)."""

    stmt = (
        select(User)
        .where(User.tenant_id == tenant_id, User.is_superuser.is_(True))
        .order_by(User.created_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def get_tenant_users_page(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    page: int,
    size: int,
    is_active: bool | None = None,
    is_superuser: bool | None = None,
) -> tuple[list[User], int]:
    """One page of a tenant's users plus the filtered total."""

    conditions = [User.tenant_id == tenant_id]
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))
    if is_superuser is not None:
        conditions.append(User.is_superuser.is_(is_superuser))
    total = db.execute(
        select(func.count()).select_from(User).where(*conditions)
    ).scalar_one()
    stmt = (
        select(User)
        .where(*conditions)
        .order_by(User.created_at.asc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return list(db.execute(stmt).scalars().all()), total


def get_tenant_admin(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> User | None:
    return (
        db.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
                User.is_superuser.is_(True),
            )
        )
        .scalars()
        .first()
    )


def count_active_admins_excluding(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    return db.execute(
        select(func.count())
        .select_from(User)
        .where(
            User.tenant_id == tenant_id,
            User.is_superuser.is_(True),
            User.is_active.is_(True),
            User.id != user_id,
        )
    ).scalar_one()


def get_usernames_with_prefix(db: Session, prefix: str) -> set[str]:
    """All usernames starting with ``prefix`` in one query (LIKE escaped)."""

    escaped = (
        prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    stmt = select(User.username).where(User.username.like(f"{escaped}%", escape="\\"))
    return set(db.execute(stmt).scalars().all())


def set_tenant_users_active(
    db: Session, tenant_id: uuid.UUID, is_active: bool
) -> list[uuid.UUID]:
    """Bulk flip one tenant's user rows; returns the affected ids (P2-W3)."""

    ids = list(
        db.execute(select(User.id).where(User.tenant_id == tenant_id)).scalars().all()
    )
    db.execute(update(User).where(User.tenant_id == tenant_id).values(is_active=is_active))
    return ids


# ---- SSO provisioning (P2-W4: login resolve / sync / delete / purge) ----


def get_user_by_external_identity(
    db: Session, tenant_id: uuid.UUID, external_id: str, external_source: str
) -> User | None:
    """Tenant user previously linked to one SSO identity."""

    return (
        db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.external_id == external_id,
                User.external_source == external_source,
            )
        )
        .scalars()
        .first()
    )


def get_user_by_email_in_tenant(
    db: Session, tenant_id: uuid.UUID, email: str
) -> User | None:
    """Tenant-scoped email lookup (OIDC first-login linking)."""

    return (
        db.execute(
            select(User).where(User.email == email, User.tenant_id == tenant_id)
        )
        .scalars()
        .first()
    )


def get_first_workspace(db: Session, tenant_id: uuid.UUID) -> Workspace | None:
    """First workspace of a tenant (host ``.filter(...).first()`` semantics)."""

    return (
        db.execute(select(Workspace).where(Workspace.tenant_id == tenant_id))
        .scalars()
        .first()
    )


def get_workspace_in_tenant(
    db: Session, workspace_id: uuid.UUID, tenant_id: uuid.UUID
) -> Workspace | None:
    return (
        db.execute(
            select(Workspace).where(
                Workspace.id == workspace_id, Workspace.tenant_id == tenant_id
            )
        )
        .scalars()
        .first()
    )


def get_member_any_state(
    db: Session, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> WorkspaceMember | None:
    """Membership lookup ignoring ``is_active`` (SSO role update)."""

    return (
        db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        .scalars()
        .first()
    )


def create_member_row(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    is_active: bool = True,
) -> WorkspaceMember:
    """Insert one ``workspace_members`` row and flush; caller owns commit."""

    member = WorkspaceMember(
        workspace_id=workspace_id, user_id=user_id, role=role, is_active=is_active
    )
    db.add(member)
    db.flush()
    return member


def delete_user_row(db: Session, user: User) -> None:
    """Mark one ``users`` row for physical deletion (caller owns commit)."""

    db.delete(user)


def delete_users_by_tenant(db: Session, tenant_id: uuid.UUID) -> int:
    """Physically delete a tenant's users and memberships; returns the user count.

    Memberships go first because ``workspace_members.user_id`` references
    ``users.id`` without a cascade; the host abort path deleted users only and
    relied on the tenant having no workspace yet.
    """

    user_ids = select(User.id).where(User.tenant_id == tenant_id)
    db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id.in_(user_ids)))
    result = db.execute(delete(User).where(User.tenant_id == tenant_id))
    return result.rowcount or 0
