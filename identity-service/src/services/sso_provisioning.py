"""SSO user/membership provisioning (P2-W4).

The SSO module (``premium/sso``, hosted by both the monolith and ops-service)
stopped writing ``users`` / ``workspace_members`` directly; identity is the
write owner of the shared auth tables (D7). These operations mirror the host
``SSOService`` semantics one-for-one, while every ORM write stays in
``repositories/management.py``.

Rejections carry a machine-readable ``reason`` because the wire-frozen
``BizCode`` mapping turns ``USER_NOT_FOUND`` into HTTP 200: callers must branch
on the reason, never on the status.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from typing import Any

from error_core.codes import BizCode
from sqlalchemy.orm import Session

from src.core.exceptions import BusinessException
from src.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
)
from src.invalidation_notify import notify_tenant_sync, notify_user_sync
from src.models.user_model import User
from src.repositories import management
from src.services import user_service, workspace_service

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_SSO_ROLES = ("manager", "member")


class ProvisioningRejected(Exception):
    """Business rejection rendered as ``{code, msg, error, reason}``."""

    def __init__(self, reason: str, code: BizCode, message: str = ""):
        self.reason = reason
        self.code = code
        self.message = message or reason
        super().__init__(self.message)


def _reject(reason: str, code: BizCode, message: str = "") -> ProvisioningRejected:
    return ProvisioningRejected(reason, code, message)


def _sync_superuser_flag(db: Session, user: User, is_admin: bool) -> bool:
    """Host ``_sync_superuser_flag``: demotion clears a membership-less workspace."""

    if user.is_superuser == is_admin:
        return False
    user.is_superuser = is_admin
    if not is_admin and user.current_workspace_id is not None:
        member = management.get_member_in_workspace(
            db, user.id, user.current_workspace_id
        )
        if member is None:
            user.current_workspace_id = None
    return True


def _create_user(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    email: str,
    username: str,
    external_id: str,
    external_source: str,
    is_superuser: bool,
    role: str,
) -> User:
    if not _EMAIL_PATTERN.match(email or ""):
        raise _reject(
            "invalid_email", BizCode.VALIDATION_FAILED, f"invalid email: {email}"
        )
    workspace = management.get_first_workspace(db, tenant_id)
    user = management.create_user_row(
        db,
        username=user_service._unique_username(db, username),
        email=email,
        hashed_password=get_password_hash(secrets.token_urlsafe(32)),
        tenant_id=tenant_id,
        is_superuser=is_superuser,
        external_id=external_id,
        external_source=external_source,
        current_workspace_id=workspace.id if workspace is not None else None,
    )
    if workspace is not None:
        management.create_member_row(
            db, workspace_id=workspace.id, user_id=user.id, role=role
        )
    return user


def _resolve_oidc(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    external_id: str,
    external_source: str,
    email: str | None,
    username: str | None,
    is_admin: bool,
    is_new_tenant: bool,
) -> User:
    user = management.get_user_by_external_identity(
        db, tenant_id, external_id, external_source
    )
    if user is not None:
        if _sync_superuser_flag(db, user, is_admin):
            db.commit()
            notify_user_sync(str(user.id))
        return user

    if email:
        user = management.get_user_by_email_in_tenant(db, tenant_id, email)
        if user is not None:
            user.external_id = external_id
            user.external_source = external_source
            _sync_superuser_flag(db, user, is_admin)
            db.commit()
            notify_user_sync(str(user.id))
            return user

    if not (is_new_tenant or is_admin):
        raise _reject(
            "user_not_found",
            BizCode.USER_NOT_FOUND,
            f"user {email or external_id} not found and not allowed to be created",
        )

    resolved_email = email or f"{external_id}@oidc.local"
    if "@" in resolved_email:
        default_username = resolved_email.split("@")[0]
    else:
        default_username = f"user_{external_id[:8]}"
    user = _create_user(
        db,
        tenant_id=tenant_id,
        email=resolved_email,
        username=username or default_username,
        external_id=external_id,
        external_source=external_source,
        is_superuser=True,
        role="manager",
    )
    db.commit()
    db.refresh(user)
    notify_user_sync(str(user.id))
    return user


def _resolve_token(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    external_id: str,
    external_source: str,
    email: str | None,
    username: str | None,
    is_new_tenant: bool,
) -> User:
    if not email:
        raise _reject("invalid_email", BizCode.VALIDATION_FAILED, "empty email")
    user = management.get_user_by_email(db, email)
    if user is not None:
        if user.tenant_id != tenant_id:
            raise _reject(
                "user_not_found", BizCode.USER_NOT_FOUND, f"user {email} not found"
            )
        return user

    if not is_new_tenant:
        raise _reject(
            "user_not_found", BizCode.USER_NOT_FOUND, f"user {email} not found"
        )

    user = _create_user(
        db,
        tenant_id=tenant_id,
        email=email,
        username=username or email.split("@")[0],
        external_id=external_id,
        external_source=external_source,
        is_superuser=True,
        role="manager",
    )
    db.commit()
    db.refresh(user)
    notify_user_sync(str(user.id))
    return user


def resolve_user(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    mode: str,
    external_id: str,
    external_source: str,
    email: str | None = None,
    username: str | None = None,
    is_admin: bool = False,
    is_new_tenant: bool = False,
) -> User:
    """Login-time resolve/create; mirrors the host ``_find_*`` user paths."""

    if management.get_tenant_by_id(db, tenant_id) is None:
        raise _reject(
            "tenant_not_found", BizCode.NOT_FOUND, f"tenant {tenant_id} not found"
        )
    if mode == "oidc":
        return _resolve_oidc(
            db,
            tenant_id=tenant_id,
            external_id=external_id,
            external_source=external_source,
            email=email,
            username=username,
            is_admin=is_admin,
            is_new_tenant=is_new_tenant,
        )
    if mode == "token":
        return _resolve_token(
            db,
            tenant_id=tenant_id,
            external_id=external_id,
            external_source=external_source,
            email=email,
            username=username,
            is_new_tenant=is_new_tenant,
        )
    raise _reject("invalid_mode", BizCode.VALIDATION_FAILED, f"unknown mode: {mode}")


def sync_users(
    db: Session, *, tenant_id: uuid.UUID, source: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Batch ensure users/memberships; per-item outcomes match the host loop."""

    if management.get_tenant_by_id(db, tenant_id) is None:
        raise _reject(
            "tenant_not_found", BizCode.NOT_FOUND, f"tenant {tenant_id} not found"
        )

    results: list[dict[str, Any]] = []
    notified: list[str] = []
    for item in items:
        external_user_id = item["external_user_id"]
        email = item["email"]
        role = item["role"]
        workspace_id = item["workspace_id"]
        base = {"external_user_id": external_user_id, "email": email}
        try:
            if role not in _SSO_ROLES:
                results.append({**base, "status": "failed", "reason": "invalid_role"})
                continue
            workspace = management.get_workspace_in_tenant(
                db, workspace_id, tenant_id
            )
            if workspace is None:
                results.append(
                    {**base, "status": "failed", "reason": "workspace_not_found"}
                )
                continue
            with db.begin_nested():
                existing = management.get_user_by_email(db, email)
                if existing is not None:
                    if existing.tenant_id != tenant_id:
                        results.append(
                            {
                                **base,
                                "status": "failed",
                                "reason": "email_used_by_other_tenant",
                            }
                        )
                        continue
                    member = management.get_member_in_workspace(
                        db, existing.id, workspace.id
                    )
                    if member is not None:
                        results.append(
                            {
                                **base,
                                "status": "exists",
                                "user_id": str(existing.id),
                            }
                        )
                        continue
                    management.create_member_row(
                        db,
                        workspace_id=workspace.id,
                        user_id=existing.id,
                        role=role,
                    )
                    results.append(
                        {**base, "status": "updated", "user_id": str(existing.id)}
                    )
                    notified.append(str(existing.id))
                    continue
                user = management.create_user_row(
                    db,
                    username=user_service._unique_username(
                        db, item.get("username") or email.split("@")[0]
                    ),
                    email=email,
                    hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                    tenant_id=tenant_id,
                    is_superuser=False,
                    external_id=external_user_id,
                    external_source=source,
                    current_workspace_id=workspace.id,
                )
                management.create_member_row(
                    db, workspace_id=workspace.id, user_id=user.id, role=role
                )
                results.append(
                    {**base, "status": "created", "user_id": str(user.id)}
                )
                notified.append(str(user.id))
        except Exception as exc:
            logger.exception("SSO 批量开户失败: email=%s", email)
            results.append({**base, "status": "failed", "message": str(exc)})

    db.commit()
    for user_id in notified:
        notify_user_sync(user_id)
    return results


def delete_users(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    source: str,
    external_user_ids: list[str],
    action: str = "disable",
) -> tuple[int, int]:
    """Batch delete/disable by external identity; returns (success, failed)."""

    if management.get_tenant_by_id(db, tenant_id) is None:
        raise _reject(
            "tenant_not_found", BizCode.NOT_FOUND, f"tenant {tenant_id} not found"
        )

    success_count, failed_count = 0, 0
    affected: list[str] = []
    for external_user_id in external_user_ids:
        user = management.get_user_by_external_identity(
            db, tenant_id, external_user_id, source
        )
        if user is None:
            failed_count += 1
            continue
        user_id = str(user.id)
        try:
            with db.begin_nested():
                if action == "delete":
                    management.delete_user_row(db, user)
                else:
                    user.is_active = False
            affected.append(user_id)
            success_count += 1
        except Exception:
            logger.exception("SSO 删除用户失败: external_user_id=%s", external_user_id)
            failed_count += 1

    db.commit()
    for user_id in affected:
        notify_user_sync(user_id)
    return success_count, failed_count


def update_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    username: str | None = None,
    email: str | None = None,
) -> User:
    """Update username/email; global email uniqueness matches the host quirk."""

    user = management.get_user_by_id_regardless_active(db, user_id)
    if user is None:
        raise _reject(
            "user_not_found", BizCode.USER_NOT_FOUND, f"user {user_id} not found"
        )
    if email and email != user.email:
        existing = management.get_user_by_email(db, email)
        if existing is not None and existing.id != user.id:
            raise _reject(
                "email_exists",
                BizCode.USER_EMAIL_ALREADY_EXISTS,
                f"email already used: {email}",
            )
        user.email = email
    if username:
        user.username = username
    try:
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        raise
    notify_user_sync(str(user.id))
    return user


def set_member_role(
    db: Session, *, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> None:
    if role not in _SSO_ROLES:
        raise _reject("invalid_role", BizCode.VALIDATION_FAILED, f"invalid role: {role}")
    if management.get_workspace_by_id(db, workspace_id) is None:
        raise _reject(
            "workspace_not_found",
            BizCode.WORKSPACE_NOT_FOUND,
            f"workspace {workspace_id} not found",
        )
    member = management.get_member_any_state(db, workspace_id, user_id)
    if member is None:
        raise _reject(
            "member_not_found",
            BizCode.NOT_FOUND,
            f"user {user_id} is not in workspace {workspace_id}",
        )
    member.role = role
    db.commit()
    notify_user_sync(str(user_id))


def remove_member(db: Session, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    if management.get_workspace_by_id(db, workspace_id) is None:
        raise _reject(
            "workspace_not_found",
            BizCode.WORKSPACE_NOT_FOUND,
            f"workspace {workspace_id} not found",
        )
    member = management.get_member_in_workspace(db, user_id, workspace_id)
    if member is None:
        raise _reject(
            "member_not_found",
            BizCode.NOT_FOUND,
            f"user {user_id} is not in workspace {workspace_id}",
        )
    member.is_active = False
    user = management.get_user_by_id_regardless_active(db, user_id)
    if user is not None:
        user.current_workspace_id = None
    db.commit()
    notify_user_sync(str(user_id))


def switch_workspace(db: Session, *, user_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
    """Delegate to the shared switch logic (membership + snapshot notify)."""

    user = management.get_user_by_id(db, user_id)
    if user is None:
        raise _reject(
            "user_not_found", BizCode.USER_NOT_FOUND, f"user {user_id} not found"
        )
    try:
        workspace_service.switch_workspace(db, workspace_id, user)
    except BusinessException as exc:
        code = exc.code if isinstance(exc.code, BizCode) else BizCode.BAD_REQUEST
        reason = (
            "workspace_not_found"
            if code == BizCode.WORKSPACE_NOT_FOUND
            else "workspace_access_denied"
        )
        raise ProvisioningRejected(reason, code, exc.message) from exc


def issue_tokens(db: Session, *, user_id: uuid.UUID) -> tuple[str, str]:
    """Sign an access/refresh pair with the shared SECRET_KEY (D10)."""

    user = management.get_user_by_id_regardless_active(db, user_id)
    if user is None:
        raise _reject(
            "user_not_found", BizCode.USER_NOT_FOUND, f"user {user_id} not found"
        )
    access_token, _ = create_access_token(subject=user.id)
    refresh_token, _ = create_refresh_token(subject=user.id)
    return access_token, refresh_token


def purge_tenant_users(db: Session, *, tenant_id: uuid.UUID) -> int:
    """Physically delete a new tenant's users (failed-provisioning compensation)."""

    try:
        deleted = management.delete_users_by_tenant(db, tenant_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if deleted:
        notify_tenant_sync(str(tenant_id))
    return deleted


__all__ = [
    "ProvisioningRejected",
    "delete_users",
    "issue_tokens",
    "purge_tenant_users",
    "remove_member",
    "resolve_user",
    "set_member_role",
    "switch_workspace",
    "sync_users",
    "update_user",
]
