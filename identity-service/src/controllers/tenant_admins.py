"""Internal tenant-admin provisioning endpoints (P2-W3).

The ops platform (and the monolith's enterprise admin routes) stopped writing
``users`` rows; these root-mounted endpoints are now the only admin/user write
entry. Callers are constrained by the identity-service NetworkPolicy, matching
the other ``/internal/*`` surfaces — no credential is checked here (decision
2026-10-23).

Errors carry a machine-readable ``reason`` extra: the wire-frozen ``BizCode``
HTTP mapping turns ``USER_NOT_FOUND`` into 200, so callers must branch on the
reason, not the status.
"""

from __future__ import annotations

import uuid

from error_core.codes import BizCode
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from src.controllers._errors import error_response
from src.core.datetime_utils import to_iso_z, to_timestamp_ms
from src.core.exceptions import BusinessException
from src.db import get_db
from src.models.user_model import User
from src.services import user_service

router = APIRouter()


class TenantAdminCreateIn(BaseModel):
    email: EmailStr
    username: str | None = Field(default=None, max_length=100)
    password: str | None = Field(default=None, min_length=6)
    preferred_language: str = Field(default="zh", max_length=10)


class TenantUsersSetActiveIn(BaseModel):
    is_active: bool


def _admin_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "created_at": to_iso_z(user.created_at),
    }


def _user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "preferred_language": user.preferred_language,
        "created_at": to_timestamp_ms(user.created_at),
        "updated_at": to_timestamp_ms(user.updated_at),
    }


def _tenant_not_found():
    return error_response(BizCode.NOT_FOUND, "tenant not found", reason="tenant_not_found")


@router.get("/internal/tenant-admins/{tenant_id}")
def list_tenant_admins(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    admins = user_service.list_tenant_admins(db, tenant_id)
    if admins is None:
        return _tenant_not_found()
    return [_admin_payload(admin) for admin in admins]


@router.post("/internal/tenant-admins/{tenant_id}")
def create_tenant_admin(
    tenant_id: uuid.UUID,
    body: TenantAdminCreateIn,
    db: Session = Depends(get_db),
):
    try:
        result = user_service.create_tenant_admin(
            db,
            tenant_id,
            email=body.email,
            username=body.username,
            password=body.password,
            preferred_language=body.preferred_language,
        )
    except BusinessException as exc:
        if exc.code == BizCode.USER_EMAIL_ALREADY_EXISTS:
            return error_response(
                BizCode.USER_EMAIL_ALREADY_EXISTS,
                "email already exists",
                reason="admin_email_exists",
            )
        raise
    if result is None:
        return _tenant_not_found()
    admin, initial_password = result
    return {**_admin_payload(admin), "initial_password": initial_password}


@router.delete("/internal/tenant-admins/{tenant_id}/{user_id}")
def remove_tenant_admin(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    try:
        removed = user_service.remove_tenant_admin(db, tenant_id, user_id)
    except BusinessException as exc:
        if exc.code == BizCode.NOT_FOUND:
            return error_response(BizCode.NOT_FOUND, "admin not found", reason="admin_not_found")
        if exc.code == BizCode.STATE_CONFLICT:
            return error_response(
                BizCode.STATE_CONFLICT,
                "last admin cannot be removed",
                reason="last_admin_cannot_be_removed",
            )
        raise
    if not removed:
        return _tenant_not_found()
    return {"removed": True}


@router.get("/internal/tenant-users/{tenant_id}")
def list_tenant_users(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    is_active: bool | None = Query(None),
    is_superuser: bool | None = Query(None),
    db: Session = Depends(get_db),
):
    result = user_service.list_tenant_users(
        db,
        tenant_id,
        page=page,
        size=size,
        is_active=is_active,
        is_superuser=is_superuser,
    )
    if result is None:
        return _tenant_not_found()
    users, total = result
    return {"items": [_user_payload(user) for user in users], "total": total}


@router.post("/internal/tenant-users/{tenant_id}/set-active")
def set_tenant_users_active(
    tenant_id: uuid.UUID,
    body: TenantUsersSetActiveIn,
    db: Session = Depends(get_db),
):
    affected = user_service.set_tenant_users_active(db, tenant_id, body.is_active)
    if affected is None:
        return _tenant_not_found()
    return {"affected": affected, "is_active": body.is_active}


@router.get("/internal/users/email-exists")
def email_exists(
    email: str = Query(min_length=3, max_length=255),
    db: Session = Depends(get_db),
):
    return {"exists": user_service.email_exists(db, email)}
