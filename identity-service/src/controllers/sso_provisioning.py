"""Internal SSO provisioning endpoints (P2-W4).

The SSO module (``premium/sso``, hosted by the monolith and ops-service) stopped
writing ``users`` / ``workspace_members`` directly; identity is the write owner
of the shared auth tables (D7). These root-mounted endpoints mirror the host
``SSOService`` semantics and are constrained by the identity-service
NetworkPolicy, matching the other ``/internal/*`` surfaces — no credential is
checked here (decision 2026-10-23).

Errors carry a machine-readable ``reason`` extra: the wire-frozen ``BizCode``
HTTP mapping turns ``USER_NOT_FOUND`` into 200, so callers must branch on the
reason, not the status.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.controllers._errors import error_response
from src.core.datetime_utils import to_timestamp_ms
from src.db import get_db
from src.models.user_model import User
from src.services import sso_provisioning
from src.services.sso_provisioning import ProvisioningRejected

router = APIRouter()


class ResolveUserIn(BaseModel):
    tenant_id: uuid.UUID
    mode: Literal["oidc", "token"]
    external_id: str = Field(min_length=1, max_length=200)
    external_source: str = Field(min_length=1, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=100)
    is_admin: bool = False
    is_new_tenant: bool = False


class SyncUserItemIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=50)
    workspace_id: uuid.UUID
    username: str | None = Field(default=None, max_length=100)


class SyncUsersIn(BaseModel):
    tenant_id: uuid.UUID
    source: str = Field(min_length=1, max_length=50)
    items: list[SyncUserItemIn]


class DeleteUsersIn(BaseModel):
    tenant_id: uuid.UUID
    source: str = Field(min_length=1, max_length=50)
    external_user_ids: list[str]
    action: Literal["delete", "disable"] = "disable"


class UpdateUserIn(BaseModel):
    username: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)


class MemberRoleIn(BaseModel):
    role: str = Field(min_length=1, max_length=50)


class SwitchWorkspaceIn(BaseModel):
    workspace_id: uuid.UUID


class IssueTokensIn(BaseModel):
    user_id: uuid.UUID


def _rejected(exc: ProvisioningRejected):
    return error_response(exc.code, exc.message, reason=exc.reason)


def _user_payload(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "tenant_id": str(user.tenant_id),
        "current_workspace_id": (
            str(user.current_workspace_id) if user.current_workspace_id else None
        ),
        "preferred_language": user.preferred_language,
        "created_at": to_timestamp_ms(user.created_at),
        "updated_at": to_timestamp_ms(user.updated_at),
    }


@router.post("/internal/sso/users/resolve")
def resolve_user(body: ResolveUserIn, db: Session = Depends(get_db)):
    try:
        user = sso_provisioning.resolve_user(
            db,
            tenant_id=body.tenant_id,
            mode=body.mode,
            external_id=body.external_id,
            external_source=body.external_source,
            email=body.email,
            username=body.username,
            is_admin=body.is_admin,
            is_new_tenant=body.is_new_tenant,
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return _user_payload(user)


@router.post("/internal/sso/users/sync")
def sync_users(body: SyncUsersIn, db: Session = Depends(get_db)):
    try:
        return sso_provisioning.sync_users(
            db,
            tenant_id=body.tenant_id,
            source=body.source,
            items=[item.model_dump() for item in body.items],
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)


@router.post("/internal/sso/users/delete")
def delete_users(body: DeleteUsersIn, db: Session = Depends(get_db)):
    try:
        success, failed = sso_provisioning.delete_users(
            db,
            tenant_id=body.tenant_id,
            source=body.source,
            external_user_ids=body.external_user_ids,
            action=body.action,
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return {"success": success, "failed": failed}


@router.put("/internal/sso/users/{user_id}")
def update_user(
    user_id: uuid.UUID, body: UpdateUserIn, db: Session = Depends(get_db)
):
    try:
        user = sso_provisioning.update_user(
            db, user_id=user_id, username=body.username, email=body.email
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return {
        "user_id": str(user.id),
        "username": user.username,
        "email": user.email,
        "updated_at": to_timestamp_ms(user.updated_at),
    }


@router.put("/internal/sso/workspaces/{workspace_id}/members/{user_id}/role")
def set_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberRoleIn,
    db: Session = Depends(get_db),
):
    try:
        sso_provisioning.set_member_role(
            db, workspace_id=workspace_id, user_id=user_id, role=body.role
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return {"updated": True}


@router.delete("/internal/sso/workspaces/{workspace_id}/members/{user_id}")
def remove_member(
    workspace_id: uuid.UUID, user_id: uuid.UUID, db: Session = Depends(get_db)
):
    try:
        sso_provisioning.remove_member(
            db, workspace_id=workspace_id, user_id=user_id
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return {"removed": True}


@router.post("/internal/sso/users/{user_id}/switch-workspace")
def switch_workspace(
    user_id: uuid.UUID, body: SwitchWorkspaceIn, db: Session = Depends(get_db)
):
    try:
        sso_provisioning.switch_workspace(
            db, user_id=user_id, workspace_id=body.workspace_id
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return {"switched": True}


@router.post("/internal/sso/tokens")
def issue_tokens(body: IssueTokensIn, db: Session = Depends(get_db)):
    try:
        access_token, refresh_token = sso_provisioning.issue_tokens(
            db, user_id=body.user_id
        )
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return {"access_token": access_token, "refresh_token": refresh_token}


@router.delete("/internal/sso/tenants/{tenant_id}/users")
def purge_tenant_users(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    try:
        deleted = sso_provisioning.purge_tenant_users(db, tenant_id=tenant_id)
    except ProvisioningRejected as exc:
        return _rejected(exc)
    return {"deleted": deleted}
