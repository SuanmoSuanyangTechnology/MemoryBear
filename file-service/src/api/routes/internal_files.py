"""Internal file API for service-to-service consumers.

Guarded by the static ``X-File-Service-Token`` header in the auth middleware;
``X-Tenant-Id`` / ``X-Workspace-Id`` / ``X-Actor-Id`` carry the caller
identity. JSON responses follow the project envelope, file bytes are streamed
raw. Every record read is scoped to the caller's tenant, and a tenant mismatch
is reported exactly like a missing record so file existence never leaks across
tenants.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...auth import Principal
from ...errors import BizCode, BusinessException
from ...models.file_metadata_model import FileMetadata
from ...response import ok
from ...services.file_record_service import FileRecordService
from ...services.file_storage_service import (
    FileStorageService,
    get_file_storage_service,
    upload_workspace_file,
)
from ...storage.exceptions import StorageDownloadError
from ..deps import require_principal

api_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1/files", tags=["internal-files"])

file_record_service = FileRecordService()

_NOT_FOUND_MSG = "The file does not exist"


class CreatePendingRequest(BaseModel):
    """A record the caller will fill with bytes itself (TTS, sandbox artifacts)."""

    file_name: str = Field(min_length=1, max_length=255)
    file_ext: str | None = Field(default=None, max_length=32)
    content_type: str | None = Field(default=None, max_length=128)
    file_size: int | None = Field(default=None, ge=0)


class CompleteFileRequest(BaseModel):
    file_size: int = Field(ge=0)


def _not_found() -> BusinessException:
    return BusinessException(_NOT_FOUND_MSG, BizCode.NOT_FOUND, 404)


def _tenant_context(principal: Principal) -> tuple[uuid.UUID, uuid.UUID | None]:
    try:
        tenant_id = uuid.UUID(principal.tenant_id)
        workspace_id = (
            uuid.UUID(principal.workspace_id) if principal.workspace_id else None
        )
    except ValueError as e:
        raise BusinessException(
            "Invalid tenant context", BizCode.UNAUTHORIZED, 401) from e
    return tenant_id, workspace_id


async def _get_owned_record(file_id: str, principal: Principal) -> FileMetadata:
    try:
        record_id = uuid.UUID(file_id)
    except ValueError as e:
        raise _not_found() from e
    record = await file_record_service.get(record_id)
    if record is None or str(record.tenant_id) != principal.tenant_id:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise _not_found()
    return record


def _upload_payload(record: FileMetadata) -> dict[str, object]:
    return {
        "file_id": str(record.id),
        "file_key": record.file_key,
        "file_name": record.file_name,
        "file_size": record.file_size,
        "content_type": record.content_type,
        "status": record.status,
    }


def _metadata_payload(record: FileMetadata) -> dict[str, object]:
    return {
        "file_id": str(record.id),
        "file_key": record.file_key,
        "file_name": record.file_name,
        "file_ext": record.file_ext,
        "file_size": record.file_size,
        "content_type": record.content_type,
        "status": record.status,
    }


@router.post("/upload")
async def upload_internal_file(
    file: Annotated[UploadFile, File()],
    principal: Annotated[Principal, Depends(require_principal)],
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
):
    """Store one caller-supplied file and return its metadata snapshot."""
    tenant_id, workspace_id = _tenant_context(principal)

    api_logger.info(
        f"Internal upload request: tenant_id={tenant_id}, "
        f"workspace_id={workspace_id}, filename={file.filename}, "
        f"actor_id={principal.user_id}"
    )

    result = await upload_workspace_file(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file=file,
        storage_service=storage_service,
    )
    record = await file_record_service.get(uuid.UUID(result["file_id"]))
    if record is None:
        api_logger.error(
            f"File record missing after upload: file_id={result['file_id']}")
        raise BusinessException(
            "File record missing after upload", BizCode.INTERNAL_ERROR, 500)

    api_logger.info(
        f"Internal upload successful: file_id={record.id}, "
        f"size={record.file_size}")
    return ok(data=_upload_payload(record), msg="File upload successful")


@router.post("/pending")
async def create_pending_file(
    body: CreatePendingRequest,
    principal: Annotated[Principal, Depends(require_principal)],
):
    """Register a pending record; the caller writes the bytes itself."""
    tenant_id, workspace_id = _tenant_context(principal)
    record = await file_record_service.create_pending(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file_name=body.file_name,
        file_ext=body.file_ext,
        content_type=body.content_type,
        file_size=body.file_size,
    )
    api_logger.info(
        f"Internal pending record created: file_id={record.id}, "
        f"tenant_id={tenant_id}, workspace_id={workspace_id}")
    return ok(data=_metadata_payload(record), msg="File record created")


@router.patch("/{file_id}/complete")
async def complete_internal_file(
    file_id: str,
    body: CompleteFileRequest,
    principal: Annotated[Principal, Depends(require_principal)],
):
    """Mark one tenant-owned record completed with the final written size."""
    record = await _get_owned_record(file_id, principal)
    updated = await file_record_service.complete(record.id, body.file_size)
    if updated is None:
        raise _not_found()
    api_logger.info(
        f"Internal file completed: file_id={updated.id}, "
        f"size={updated.file_size}")
    return ok(data=_metadata_payload(updated), msg="File marked as completed")


@router.patch("/{file_id}/failed")
async def fail_internal_file(
    file_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
):
    """Mark one tenant-owned record failed; the stored object is left untouched."""
    record = await _get_owned_record(file_id, principal)
    updated = await file_record_service.fail(record.id)
    if updated is None:
        raise _not_found()
    api_logger.info(f"Internal file marked as failed: file_id={updated.id}")
    return ok(data=_metadata_payload(updated), msg="File marked as failed")


@router.get("/{file_id}/metadata")
async def get_internal_file_metadata(
    file_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
):
    """Return one file's metadata; cross-tenant ids are reported as missing."""
    record = await _get_owned_record(file_id, principal)
    return ok(
        data=_metadata_payload(record),
        msg="File metadata retrieved successfully",
    )


@router.get("/{file_id}/content")
async def get_internal_file_content(
    file_id: str,
    principal: Annotated[Principal, Depends(require_principal)],
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
) -> StreamingResponse:
    """Stream the stored bytes with an attachment disposition."""
    record = await _get_owned_record(file_id, principal)
    if record.status != "completed":
        raise _not_found()
    if not await storage_service.file_exists(record.file_key):
        api_logger.warning(
            f"File object missing in storage: file_key={record.file_key}")
        raise BusinessException("File not found", BizCode.NOT_FOUND, 404)

    stream = storage_service.storage.download_stream(record.file_key)
    try:
        # Open the stream before sending headers so a backend failure still
        # produces the project's error envelope instead of a broken 200.
        first_chunk = await stream.__anext__()
    except StopAsyncIteration:
        first_chunk = None
    except FileNotFoundError:
        raise BusinessException("File not found", BizCode.NOT_FOUND, 404) from None
    except StorageDownloadError as e:
        api_logger.error(f"Failed to stream file: {e}")
        raise BusinessException(
            "Failed to retrieve file", BizCode.INTERNAL_ERROR, 500) from e

    async def _body():
        if first_chunk is not None:
            yield first_chunk
        async for chunk in stream:
            yield chunk

    encoded_filename = quote(record.file_name)
    return StreamingResponse(
        _body(),
        media_type=record.content_type or "application/octet-stream",
        headers={
            "Content-Disposition":
                f"attachment; filename*=UTF-8''{encoded_filename}"
        },
    )
