"""Workspace reads/writes for the management plane (sync, host-compatible).

Task 10 seeds the invite-binding needs of the login path; Task 12 appends the
full workspace/member/invite surface. Guard reads live in ``management``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.datetime_utils import utcnow_naive
from src.models.user_model import User
from src.models.workspace_model import (
    InviteStatus,
    Workspace,
    WorkspaceDefaultModelPreset,
    WorkspaceInvite,
    WorkspaceMember,
    WorkspaceRole,
)
from src.repositories.end_user_repository import mark_grace_period_for_expired_users
from src.schemas.workspace_schema import WorkspaceCreate, WorkspaceInviteCreate

logger = logging.getLogger(__name__)

_DEFAULT_PRESET_KEY = "default"
_RETENTION_GRACE_PERIOD_DAYS = 3


def get_workspaces_by_user(db: Session, user_id: uuid.UUID) -> list[Workspace]:
    """Host semantics: superusers see every active workspace of the tenant."""

    user = db.get(User, user_id)
    if user is None:
        return []

    stmt = select(Workspace).where(Workspace.is_active.is_(True))
    if user.is_superuser:
        stmt = stmt.where(Workspace.tenant_id == user.tenant_id)
    else:
        stmt = stmt.join(
            WorkspaceMember, Workspace.id == WorkspaceMember.workspace_id
        ).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.is_active.is_(True),
        )
    return list(db.execute(stmt.order_by(Workspace.updated_at.desc())).scalars())


def add_member_to_workspace(
    db: Session,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    role: str = WorkspaceRole.member,
) -> WorkspaceMember:
    member = WorkspaceMember(user_id=user_id, workspace_id=workspace_id, role=role)
    db.add(member)
    db.flush()
    return member


def get_invite_by_token_hash(db: Session, token_hash: str) -> WorkspaceInvite | None:
    return (
        db.execute(
            select(WorkspaceInvite).where(WorkspaceInvite.token_hash == token_hash)
        )
        .scalars()
        .first()
    )


def update_invite_status(
    db: Session,
    invite_id: uuid.UUID,
    status: InviteStatus,
    accepted_at: datetime | None = None,
) -> WorkspaceInvite | None:
    """Host ``WorkspaceInviteRepository.update_invite_status``（内部 commit）。"""

    invite = db.get(WorkspaceInvite, invite_id)
    if invite is None:
        return None
    invite.status = status
    if accepted_at:
        invite.accepted_at = accepted_at
    invite.updated_at = utcnow_naive()
    db.commit()
    db.refresh(invite)
    return invite


def get_default_model_preset(db: Session) -> WorkspaceDefaultModelPreset | None:
    """Host singleton preset row used to fill the six model slots."""

    return (
        db.execute(
            select(WorkspaceDefaultModelPreset)
            .where(WorkspaceDefaultModelPreset.singleton_key == _DEFAULT_PRESET_KEY)
            .limit(1)
        )
        .scalars()
        .first()
    )


def create_workspace(
    db: Session, *, workspace: WorkspaceCreate, tenant_id: uuid.UUID
) -> Workspace:
    """Host ``create_workspace``: insert + flush; the caller owns the commit."""

    new_workspace = Workspace(
        name=workspace.name,
        description=workspace.description,
        icon=workspace.icon,
        iconType=workspace.iconType,
        storage_type=workspace.storage_type or "neo4j",
        tenant_id=tenant_id,
        is_default_config=workspace.is_default_config,
        llm=workspace.llm,
        embedding=workspace.embedding,
        rerank=workspace.rerank,
        vision=workspace.vision,
        audio=workspace.audio,
        video=workspace.video,
        default_model_notice_pending=False,
    )
    db.add(new_workspace)
    db.flush()
    return new_workspace


def get_workspaces_by_name(
    db: Session, *, tenant_id: uuid.UUID, name: str
) -> list[Workspace]:
    """Host duplicate-name lookup: tenant + name + active."""

    return (
        db.execute(
            select(Workspace).where(
                Workspace.tenant_id == tenant_id,
                Workspace.name == name,
                Workspace.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )


def get_workspace_retention_days(db: Session, workspace_id: uuid.UUID) -> int | None:
    return db.scalar(
        select(Workspace.retention_days).where(Workspace.id == workspace_id)
    )


def update_workspace_retention_days(
    db: Session, workspace_id: uuid.UUID, retention_days: int | None
) -> int | None:
    """Host semantics: row lock, grace marking on shrink/first set, single commit."""

    try:
        workspace = (
            db.execute(
                select(Workspace)
                .where(Workspace.id == workspace_id)
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if workspace is None:
            raise ValueError(f"Workspace not found: {workspace_id}")

        previous_retention_days = workspace.retention_days
        workspace.retention_days = retention_days
        if retention_days is not None and (
            previous_retention_days is None or retention_days < previous_retention_days
        ):
            reference_time = utcnow_naive()
            mark_grace_period_for_expired_users(
                db,
                workspace_id=workspace_id,
                retention_days=retention_days,
                grace_period_until=reference_time
                + timedelta(days=_RETENTION_GRACE_PERIOD_DAYS),
                reference_time=reference_time,
            )

        db.commit()
        db.refresh(workspace)
        return workspace.retention_days
    except Exception as exc:
        logger.error("更新工作空间保留天数失败: workspace_id=%s - %s", workspace_id, exc)
        db.rollback()
        raise


def get_members_by_workspace(
    db: Session, workspace_id: uuid.UUID
) -> list[tuple[WorkspaceMember, User]]:
    """Host join: active members with active users (identity models avoid relations)."""

    stmt = (
        select(WorkspaceMember, User)
        .join(User, WorkspaceMember.user_id == User.id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    return db.execute(stmt).all()


def get_member_by_id(
    db: Session, member_id: uuid.UUID
) -> tuple[WorkspaceMember, User] | None:
    """Host join: active member with active user only."""

    stmt = (
        select(WorkspaceMember, User)
        .join(User, WorkspaceMember.user_id == User.id)
        .where(
            WorkspaceMember.id == member_id,
            WorkspaceMember.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    return db.execute(stmt).first()


def update_member_role_by_id(
    db: Session, member_id: uuid.UUID, role: str
) -> WorkspaceMember | None:
    """Host semantics: internal commit per member update."""

    member = (
        db.execute(select(WorkspaceMember).where(WorkspaceMember.id == member_id))
        .scalars()
        .first()
    )
    if member is None:
        return None
    member.role = role
    db.commit()
    db.refresh(member)
    return member


def count_user_active_memberships_excluding(
    db: Session, user_id: uuid.UUID, excluded_workspace_id: uuid.UUID
) -> int:
    stmt = (
        select(func.count())
        .select_from(WorkspaceMember)
        .where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.workspace_id != excluded_workspace_id,
            WorkspaceMember.is_active.is_(True),
        )
    )
    return db.execute(stmt).scalar_one()


def get_pending_invite_by_email_and_workspace(
    db: Session, email: str, workspace_id: uuid.UUID
) -> WorkspaceInvite | None:
    stmt = select(WorkspaceInvite).where(
        WorkspaceInvite.email == email,
        WorkspaceInvite.workspace_id == workspace_id,
        WorkspaceInvite.status == InviteStatus.pending,
    )
    return db.execute(stmt).scalars().first()


def create_invite(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    invite_data: WorkspaceInviteCreate,
    token_hash: str,
    created_by_user_id: uuid.UUID,
) -> WorkspaceInvite:
    """Host ``WorkspaceInviteRepository.create_invite``（内部 commit）。"""

    invite = WorkspaceInvite(
        workspace_id=workspace_id,
        email=invite_data.email,
        role=invite_data.role,
        token_hash=token_hash,
        status=InviteStatus.pending,
        expires_at=utcnow_naive() + timedelta(days=invite_data.expires_in_days),
        created_by_user_id=created_by_user_id,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def get_workspace_invites(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    status: InviteStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WorkspaceInvite]:
    stmt = select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == workspace_id)
    if status is not None:
        stmt = stmt.where(WorkspaceInvite.status == status)
    stmt = stmt.order_by(WorkspaceInvite.created_at.desc()).offset(offset).limit(limit)
    return db.execute(stmt).scalars().all()


def revoke_invite(db: Session, invite_id: uuid.UUID) -> WorkspaceInvite | None:
    """Host quirk kept verbatim: revocation commits before the caller checks
    workspace ownership, so a mismatched revoke still persists."""

    return update_invite_status(db, invite_id, InviteStatus.revoked)
