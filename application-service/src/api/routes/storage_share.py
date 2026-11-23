"""Shared-link storage upload adapter (host ``file_storage_controller`` parity).

The public share viewer holds no platform credentials; the share access token
(resolved by ``get_share_user_id``) is the only identity. It maps to a release
share, then to the active app and its workspace, which supply the tenant and
workspace scope. The transfer itself is delegated to the file service, which
owns the metadata record and the storage backend; this service writes neither.

The path is exempted from the platform identity pipeline in ``auth.py`` (the
gateway forwards it with the share token untouched, same contract as
``/internal/v1/public/share``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ...config import current_settings
from ...db import get_async_db_context
from ...errors import BizCode, BusinessException
from ...integrations.file_service import get_file_service_client
from ...models import App, Workspace
from ...repositories.release_share_repository import ReleaseShareRepository
from ..dependencies import ShareTokenData, get_share_user_id
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/storage/share", tags=["Storage Share"])

logger = logging.getLogger(__name__)

ShareViewer = Annotated[ShareTokenData, Depends(get_share_user_id)]


async def _resolve_share_scope(
    share_token: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]:
    """Resolve tenant / workspace / actor from the share token.

    Mirrors the monolith chain and its 404 wording: the share row, then the
    active app, then the owning workspace. The 404 stays framework-shaped
    (same precedent as the workspace guard) because the legacy ``NOT_FOUND``
    code maps to HTTP 400 on the wire, while the host surface answers 404.
    """

    async with get_async_db_context() as adb:
        share = await ReleaseShareRepository(adb).get_by_share_token_async(share_token)
        if share is None:
            raise HTTPException(status_code=404, detail="Shared app not found")
        app = await adb.get(App, share.app_id)
        if app is None or not app.is_active:
            raise HTTPException(status_code=404, detail="App not found")
        workspace = await adb.get(Workspace, app.workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return workspace.tenant_id, app.workspace_id, share.created_by


@router.post("/files", summary="分享链接文件上传", response_model=ApiResponse)
async def upload_shared_file(
    file: Annotated[UploadFile, File()],
    share_data: ShareViewer,
):
    """Upload one file on behalf of a shared app (share-token authenticated)."""

    tenant_id, workspace_id, actor_id = await _resolve_share_scope(
        share_data.share_token
    )

    # Host contract reads the whole body for this surface (the monolith did the
    # same); chat attachments take the streaming surface instead.
    content = await file.read()
    max_file_size = current_settings().max_file_size

    if not content:
        raise BusinessException("The file is empty.", BizCode.BAD_REQUEST)

    # The monolith answers 400 on this surface (unlike the chat upload, 413).
    if len(content) > max_file_size:
        raise BusinessException(
            f"The file size exceeds the {max_file_size} byte limit",
            BizCode.BAD_REQUEST,
        )

    logger.info(
        f"Storage upload request (share): tenant_id={tenant_id}, "
        f"workspace_id={workspace_id}, filename={file.filename}"
    )

    result = await get_file_service_client().upload(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        file_name=file.filename or "",
        content_type=file.content_type,
        content=content,
    )

    logger.info(
        f"File upload successful (share): file_id={result.file_id}, "
        f"size={result.file_size}"
    )
    return success(
        data={"file_id": result.file_id, "file_key": result.file_key},
        msg="File upload successful",
    )


__all__ = ["router"]
