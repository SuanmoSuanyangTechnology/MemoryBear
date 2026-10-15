"""Release-share routes (host ``release_share_controller`` parity).

Workspace-guarded management face for a release's public share configuration:
create-or-update, update, read, delete and token regeneration.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...guards.workspace_access import require_workspace_access
from ...schemas import release_share_schema
from ...services.release_share_service import ReleaseShareService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/apps", tags=["Release Share"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]


def get_base_url(request: Request) -> str:
    """Public origin of the original request.

    Behind the gateway this service only sees its own internal URL, so the
    origin is rebuilt from the ``X-Forwarded-Proto`` / ``X-Forwarded-Host``
    headers the gateway injects, falling back to the request origin when they
    are absent (direct calls in tests / local runs).
    """

    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}"


@router.post(
    "/{app_id}/releases/{release_id}/share",
    summary="创建/启用分享配置",
    response_model=ApiResponse,
)
def create_share(
    app_id: UUID,
    release_id: UUID,
    payload: release_share_schema.ReleaseShareCreate,
    request: Request,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Create the share configuration, or overwrite the existing one."""

    base_url = get_base_url(request)

    service = ReleaseShareService(db)
    share = service.create_or_update_share(
        release_id=release_id,
        user_id=principal.actor_id,
        workspace_id=workspace_id,
        data=payload,
    )

    return success(data=service.convert_to_schema(share, base_url), msg="分享配置已创建")


@router.put(
    "/{app_id}/releases/{release_id}/share",
    summary="更新分享配置",
    response_model=ApiResponse,
)
def update_share(
    app_id: UUID,
    release_id: UUID,
    payload: release_share_schema.ReleaseShareUpdate,
    request: Request,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """Update the share configuration; the share token is left untouched."""

    base_url = get_base_url(request)

    service = ReleaseShareService(db)
    share = service.update_share(release_id=release_id, workspace_id=workspace_id, data=payload)

    return success(data=service.convert_to_schema(share, base_url), msg="分享配置已更新")


@router.get(
    "/{app_id}/releases/{release_id}/share",
    summary="获取分享配置",
    response_model=ApiResponse,
)
def get_share(
    app_id: UUID,
    release_id: UUID,
    request: Request,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """Read the share configuration; null when none exists."""

    service = ReleaseShareService(db)
    share = service.get_share(
        release_id=release_id,
        workspace_id=workspace_id,
        base_url=get_base_url(request),
    )

    return success(data=share)


@router.delete(
    "/{app_id}/releases/{release_id}/share",
    summary="删除分享配置",
    response_model=ApiResponse,
)
def delete_share(
    app_id: UUID,
    release_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """Delete the share configuration (the public link stops resolving)."""

    service = ReleaseShareService(db)
    service.delete_share(release_id=release_id, workspace_id=workspace_id)

    return success(msg="分享配置已删除")


@router.post(
    "/{app_id}/releases/{release_id}/share/regenerate-token",
    summary="重新生成分享链接",
    response_model=ApiResponse,
)
def regenerate_token(
    app_id: UUID,
    release_id: UUID,
    request: Request,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """Mint a fresh share token, invalidating the previous link."""

    base_url = get_base_url(request)

    service = ReleaseShareService(db)
    share = service.regenerate_token(release_id=release_id, workspace_id=workspace_id)

    return success(data=service.convert_to_schema(share, base_url), msg="分享链接已重新生成")
