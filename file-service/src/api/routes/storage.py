"""Storage routes (port of the monolith ``file_storage_controller``).

The nine handlers mirror ``core/api/app/controllers/file_storage_controller.py``
one-for-one; only the session style changed: metadata reads/writes go through
``FileRecordService``'s short async sessions instead of a request-scoped sync
``Session``, so no connection is held while a file body is streamed.

Upload, download and delete require an identity (``require_principal``); the
six public reads are exempted by the auth middleware (``_is_public_storage``)
and intentionally declare no principal dependency. ``info-by-url`` additionally
applies SSRF checks before any outbound request.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import mimetypes
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from ...auth import Principal
from ...config import settings
from ...errors import BizCode, BusinessException
from ...models.file_metadata_model import FileMetadata
from ...response import ok
from ...services.file_record_service import FileRecordService
from ...services.file_storage_service import (
    FileStorageService,
    get_file_storage_service,
    upload_workspace_file,
)
from ...services.url_signer import generate_signed_url, verify_signed_url
from ...storage import LocalStorage, MinIOStorage
from ...storage.exceptions import StorageDeleteError
from ..deps import require_principal

api_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1/storage", tags=["storage"])

file_record_service = FileRecordService()

_NOT_FOUND_MSG = "The file does not exist"
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
_MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _not_found() -> BusinessException:
    return BusinessException(_NOT_FOUND_MSG, BizCode.NOT_FOUND, 404)


def _ensure_completed(record: FileMetadata) -> None:
    if record.status != "completed":
        raise BusinessException(
            f"File upload not completed, status: {record.status}",
            BizCode.BAD_REQUEST,
        )


async def _get_completed_record(file_id: uuid.UUID) -> FileMetadata:
    record = await file_record_service.get(file_id)
    if record is None:
        raise _not_found()
    _ensure_completed(record)
    return record


def _permanent_local_url(file_id: uuid.UUID) -> str:
    return settings.file_local_server_url.rstrip("/") + f"/storage/permanent/{file_id}"


def _match_scheme(request: Request, url: str) -> str:
    """
    将 presigned URL 的协议替换为与当前请求一致的协议（http/https）。
    解决反向代理场景下 presigned URL 协议与请求协议不匹配的问题。
    """
    incoming_scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    if url.startswith("http://") and incoming_scheme == "https":
        return "https://" + url[7:]
    if url.startswith("https://") and incoming_scheme == "http":
        return "http://" + url[8:]
    return url


async def _stream_remote_file(
    storage: MinIOStorage,
    file_metadata: FileMetadata,
    file_key: str,
) -> StreamingResponse:
    """
    通过 API 流式回传 MinIO 上的文件内容。

    仅用于 MinIO：其 presigned URL 指向内网端点（如 http://10.x.x.x:9000），
    浏览器不可达且不支持 TLS，走 302 重定向会触发 ERR_SSL_PROTOCOL_ERROR。
    故改为后端代理流式回传，浏览器只与本服务通信。S3/OSS 的 presigned URL
    为公网 HTTPS，仍走重定向，不走此方法。

    先预取首个 chunk：MinIO 上文件缺失时能在响应发出前抛出 404，而不是
    发出 200 后再中断流。
    """
    content_type = (
        file_metadata.content_type
        or mimetypes.guess_type(file_metadata.file_name)[0]
        or "application/octet-stream"
    )
    encoded_filename = quote(file_metadata.file_name)

    stream = storage.download_stream(file_key)
    try:
        first_chunk = await stream.__anext__()
    except StopAsyncIteration:
        first_chunk = None  # 空对象

    async def _body():
        if first_chunk is not None:
            yield first_chunk
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        _body(),
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


def _is_forbidden_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _assert_public_url(url: str) -> None:
    """Reject non-http(s) URLs and hosts resolving to non-public addresses.

    Every address family is checked and one forbidden answer rejects the URL.
    ``socket.getaddrinfo`` is blocking, so it runs on the default executor.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.hostname:
        raise BusinessException(
            "Only http/https URLs are supported", BizCode.BAD_REQUEST)
    if settings.file_info_allow_private_networks:
        return
    loop = asyncio.get_running_loop()
    try:
        addresses = await loop.run_in_executor(
            None, socket.getaddrinfo, parsed.hostname, None)
    except OSError as e:
        raise BusinessException(
            "Failed to resolve file URL host", BizCode.BAD_REQUEST) from e
    for info in addresses:
        try:
            forbidden = _is_forbidden_address(info[4][0])
        except ValueError:
            forbidden = True
        if forbidden:
            raise BusinessException(
                "Access to private network addresses is not allowed",
                BizCode.BAD_REQUEST,
            )


async def _request_following_redirects(
    request: Callable[[str], Awaitable[httpx.Response]],
    url: str,
) -> httpx.Response:
    """Issue ``request(url)`` and follow up to ``_MAX_REDIRECTS`` redirects.

    Every ``Location`` target is re-validated with ``_assert_public_url`` before
    the next request is issued, so a public host cannot bounce the probe onto
    an internal address. The HTTP method is preserved across hops and a 3xx
    response without a ``Location`` header is returned as the final response.
    """
    current_url = url
    redirects = 0
    while True:
        response = await request(current_url)
        location = response.headers.get("Location")
        if response.status_code not in _REDIRECT_STATUS_CODES or not location:
            return response
        redirects += 1
        if redirects > _MAX_REDIRECTS:
            raise BusinessException("Too many redirects", BizCode.BAD_REQUEST)
        next_url = urljoin(current_url, location)
        await _assert_public_url(next_url)
        current_url = next_url


@router.post("/files")
async def upload_file(
    file: Annotated[UploadFile, File()],
    principal: Annotated[Principal, Depends(require_principal)],
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
):
    """Upload a file to the configured storage backend."""
    try:
        tenant_id = uuid.UUID(principal.tenant_id)
        workspace_id = (
            uuid.UUID(principal.workspace_id) if principal.workspace_id else None
        )
    except ValueError as e:
        raise BusinessException(
            "Invalid tenant context", BizCode.UNAUTHORIZED, 401) from e

    api_logger.info(
        f"Storage upload request: tenant_id={tenant_id}, workspace_id={workspace_id}, "
        f"filename={file.filename}, user_id={principal.user_id}"
    )

    upload_result = await upload_workspace_file(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file=file,
        storage_service=storage_service,
    )

    api_logger.info(
        f"File upload successful: {file.filename} (file_id: {upload_result['file_id']})"
    )

    return ok(data=upload_result, msg="File upload successful")


@router.get("/files/info-by-url")
async def get_file_info_by_url(url: str):
    """Get file information by network URL (no authentication required)."""
    api_logger.info(f"File info by URL request: url={url}")
    await _assert_public_url(url)

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            # Try HEAD request first
            response = await _request_following_redirects(client.head, url)

            # Some servers don't support HEAD; fall back to GET
            if response.status_code != 200:
                api_logger.info(
                    f"HEAD request failed with {response.status_code}, trying GET request")
                response = await _request_following_redirects(client.get, url)

                if response.status_code != 200:
                    api_logger.error(
                        f"Failed to fetch file info: HTTP {response.status_code}")
                    raise BusinessException(
                        f"Unable to access file: HTTP {response.status_code}",
                        BizCode.BAD_REQUEST,
                    )

            file_size = response.headers.get("Content-Length")
            if file_size:
                file_size = int(file_size)
            elif hasattr(response, "content"):
                file_size = len(response.content)
            else:
                file_size = None

            content_type = response.headers.get(
                "Content-Type", "application/octet-stream")
            content_type = content_type.split(";")[0].strip()

            file_name = None
            content_disposition = response.headers.get("Content-Disposition")
            if content_disposition and "filename=" in content_disposition:
                parts = content_disposition.split("filename=")
                if len(parts) > 1:
                    file_name = parts[1].strip('"').strip("'")

            if not file_name:
                parsed_url = urlparse(url)
                file_name = unquote(os.path.basename(parsed_url.path)) or "unknown"

            _, file_ext = os.path.splitext(file_name)

            # If no extension found, infer from content type
            if not file_ext:
                ext = mimetypes.guess_extension(content_type)
                if ext:
                    file_ext = ext
                    file_name = f"{file_name}{file_ext}"

            api_logger.info(
                f"File info retrieved: name={file_name}, size={file_size}, "
                f"type={content_type}")

            return ok(
                data={
                    "url": url,
                    "file_name": file_name,
                    "file_ext": file_ext.lower() if file_ext else "",
                    "file_size": file_size,
                    "content_type": content_type,
                },
                msg="File information retrieved successfully",
            )
    except BusinessException:
        raise
    except Exception as e:
        api_logger.error(f"Unexpected error: {e}")
        raise BusinessException(
            "Failed to retrieve file information",
            BizCode.INTERNAL_ERROR,
            500,
        ) from e


@router.get("/files/{file_id}/status")
async def get_file_status(file_id: uuid.UUID):
    """Get file upload/processing status (no authentication required)."""
    api_logger.info(f"File status request: file_id={file_id}")

    file_metadata = await file_record_service.get(file_id)
    if file_metadata is None:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise _not_found()

    return ok(
        data={
            "file_id": str(file_id),
            "status": file_metadata.status,
            "file_name": file_metadata.file_name,
            "file_size": file_metadata.file_size,
            "content_type": file_metadata.content_type,
        },
        msg="File status retrieved successfully",
    )


@router.get("/files/{file_id}/url")
async def get_file_url(
    request: Request,
    file_id: uuid.UUID,
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
    expires: int | None = None,
    permanent: bool = False,
):
    """Get an access URL for a file (no authentication required)."""
    if expires is None:
        expires = settings.file_url_expires

    api_logger.info(
        f"Get file URL request: file_id={file_id}, expires={expires}, "
        f"permanent={permanent}")

    file_metadata = await _get_completed_record(file_id)
    file_key = file_metadata.file_key
    storage = storage_service.storage

    try:
        if permanent:
            url = _permanent_local_url(file_id)
            return ok(
                data={
                    "url": url,
                    "expires_in": None,
                    "permanent": True,
                    "file_name": file_metadata.file_name,
                },
                msg="Permanent file URL generated successfully",
            )

        if isinstance(storage, LocalStorage):
            url = generate_signed_url(str(file_id), expires)
        else:
            url = await storage_service.get_file_url(
                file_key,
                expires=expires,
                file_name=file_metadata.file_name,
            )
            url = _match_scheme(request, url)

        api_logger.info(f"Generated file URL: file_id={file_id}")
        return ok(
            data={
                "url": url,
                "expires_in": expires,
                "permanent": False,
                "file_name": file_metadata.file_name,
            },
            msg="File URL generated successfully",
        )
    except Exception as e:
        api_logger.error(f"Failed to generate file URL: {e}")
        raise BusinessException(
            "Failed to generate file URL", BizCode.INTERNAL_ERROR, 500) from e


@router.get("/files/{file_id}/public-url")
async def get_permanent_file_url(
    file_id: uuid.UUID,
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
):
    """Get a permanent public URL (no expiration, no authentication)."""
    file_metadata = await _get_completed_record(file_id)
    file_key = file_metadata.file_key
    storage = storage_service.storage

    try:
        if isinstance(storage, LocalStorage):
            url = _permanent_local_url(file_id)
        else:
            url = await storage.get_permanent_url(file_key)
            if not url:
                raise BusinessException(
                    "Permanent URL not supported for current storage backend",
                    BizCode.BAD_REQUEST,
                    501,
                )

        api_logger.info(f"Generated permanent URL: file_id={file_id}")
        return ok(
            data={
                "url": url,
                "expires_in": None,
                "permanent": True,
                "file_name": file_metadata.file_name,
            },
            msg="Permanent file URL generated successfully",
        )
    except BusinessException:
        raise
    except Exception as e:
        api_logger.error(f"Failed to generate permanent URL: {e}")
        raise BusinessException(
            "Failed to generate permanent URL",
            BizCode.INTERNAL_ERROR,
            500,
        ) from e


@router.get("/files/{file_id}")
async def download_file(
    request: Request,
    file_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
) -> Any:
    """Download a file from the configured storage backend."""
    api_logger.info(f"Storage download request: file_id={file_id}")

    file_metadata = await file_record_service.get(file_id)
    if file_metadata is None or str(file_metadata.tenant_id) != principal.tenant_id:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise _not_found()

    _ensure_completed(file_metadata)

    file_key = file_metadata.file_key
    storage = storage_service.storage

    if isinstance(storage, LocalStorage):
        full_path = storage._get_full_path(file_key)

        if not full_path.exists():
            api_logger.warning(f"File not found on disk: file_key={file_key}")
            raise BusinessException(
                "File not found (possibly deleted)", BizCode.NOT_FOUND, 404)

        api_logger.info(f"Serving local file: file_key={file_key}")
        encoded_filename = quote(file_metadata.file_name)
        return FileResponse(
            path=str(full_path),
            media_type=file_metadata.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            },
        )
    elif isinstance(storage, MinIOStorage):
        # MinIO lives on an intranet; stream through the backend instead of redirecting.
        try:
            return await _stream_remote_file(storage, file_metadata, file_key)
        except FileNotFoundError:
            api_logger.warning(f"File not found in MinIO: file_key={file_key}")
            raise BusinessException(
                "File not found in storage", BizCode.NOT_FOUND, 404) from None
        except Exception as e:
            api_logger.error(f"Failed to stream file from MinIO: {e}")
            raise BusinessException(
                "Failed to retrieve file", BizCode.INTERNAL_ERROR, 500) from e
    else:
        # S3/OSS presigned URLs are public HTTPS; keep the 302 redirect.
        try:
            presigned_url = await storage_service.get_file_url(file_key, expires=3600)
            presigned_url = _match_scheme(request, presigned_url)
            api_logger.info(f"Redirecting to presigned URL: file_key={file_key}")
            return RedirectResponse(url=presigned_url, status_code=302)
        except FileNotFoundError:
            api_logger.warning(f"File not found in remote storage: file_key={file_key}")
            raise BusinessException(
                "File not found in storage", BizCode.NOT_FOUND, 404) from None
        except Exception as e:
            api_logger.error(f"Failed to get presigned URL: {e}")
            raise BusinessException(
                "Failed to retrieve file", BizCode.INTERNAL_ERROR, 500) from e


@router.delete("/files/{file_id}")
async def delete_file(
    file_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
):
    """Delete a file from the configured storage backend."""
    api_logger.info(
        f"Storage delete request: file_id={file_id}, user_id={principal.user_id}")

    file_metadata = await file_record_service.get(file_id)
    if file_metadata is None or str(file_metadata.tenant_id) != principal.tenant_id:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise _not_found()

    file_key = file_metadata.file_key

    try:
        deleted = await storage_service.delete_file(file_key)
        if deleted:
            api_logger.info(f"File deleted from storage: file_key={file_key}")
        else:
            api_logger.info(f"File did not exist in storage: file_key={file_key}")
    except StorageDeleteError as e:
        api_logger.error(f"Storage delete failed: {e}")
        raise BusinessException(
            "Failed to delete file from storage",
            BizCode.INTERNAL_ERROR,
            500,
        ) from e

    try:
        await file_record_service.delete(file_id)
        api_logger.info(f"File record deleted from database: file_id={file_id}")
    except Exception as e:
        api_logger.error(f"Database delete failed: {e}")
        raise BusinessException(
            "Failed to delete file record", BizCode.INTERNAL_ERROR, 500) from e

    return ok(msg="File deleted successfully")


@router.get("/public/{file_id}")
async def public_download_file(
    request: Request,
    file_id: uuid.UUID,
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
    expires: int = 0,
    signature: str = "",
) -> Any:
    """Public download with signature verification (no authentication)."""
    api_logger.info(f"Public download request: file_id={file_id}")

    is_valid, error_msg = verify_signed_url(str(file_id), expires, signature)
    if not is_valid:
        api_logger.warning(
            f"Invalid signed URL: file_id={file_id}, error={error_msg}")
        raise BusinessException(
            error_msg or "Invalid signature", BizCode.FORBIDDEN, 403)

    file_metadata = await _get_completed_record(file_id)
    file_key = file_metadata.file_key
    storage = storage_service.storage

    if isinstance(storage, LocalStorage):
        full_path = storage._get_full_path(file_key)

        if not full_path.exists():
            api_logger.warning(f"File not found on disk: file_key={file_key}")
            raise BusinessException("File not found", BizCode.NOT_FOUND, 404)

        api_logger.info(f"Serving public file: file_key={file_key}")
        encoded_filename = quote(file_metadata.file_name)
        return FileResponse(
            path=str(full_path),
            media_type=file_metadata.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            },
        )
    elif isinstance(storage, MinIOStorage):
        # MinIO lives on an intranet; stream through the backend instead of redirecting.
        try:
            return await _stream_remote_file(storage, file_metadata, file_key)
        except FileNotFoundError:
            api_logger.warning(f"File not found in MinIO: file_key={file_key}")
            raise BusinessException(
                "File not found in storage", BizCode.NOT_FOUND, 404) from None
        except Exception as e:
            api_logger.error(f"Failed to stream file from MinIO: {e}")
            raise BusinessException(
                "Failed to retrieve file", BizCode.INTERNAL_ERROR, 500) from e
    else:
        # S3/OSS presigned URLs are public HTTPS; keep the 302 redirect.
        try:
            presigned_url = await storage_service.get_file_url(file_key, expires=3600)
            presigned_url = _match_scheme(request, presigned_url)
            return RedirectResponse(url=presigned_url, status_code=302)
        except Exception as e:
            api_logger.error(f"Failed to get presigned URL: {e}")
            raise BusinessException(
                "Failed to retrieve file", BizCode.INTERNAL_ERROR, 500) from e


@router.get("/permanent/{file_id}")
async def permanent_download_file(
    request: Request,
    file_id: uuid.UUID,
    storage_service: Annotated[FileStorageService, Depends(get_file_storage_service)],
) -> Any:
    """Permanent download (no expiration, no signature required)."""
    api_logger.info(f"Permanent download request: file_id={file_id}")

    file_metadata = await _get_completed_record(file_id)
    file_key = file_metadata.file_key
    storage = storage_service.storage

    if isinstance(storage, LocalStorage):
        full_path = storage._get_full_path(file_key)

        if not full_path.exists():
            api_logger.warning(f"File not found on disk: file_key={file_key}")
            raise BusinessException("File not found", BizCode.NOT_FOUND, 404)

        api_logger.info(f"Serving permanent file: file_key={file_key}")
        encoded_filename = quote(file_metadata.file_name)
        return FileResponse(
            path=str(full_path),
            media_type=file_metadata.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            },
        )
    elif isinstance(storage, MinIOStorage):
        # MinIO lives on an intranet; stream through the backend instead of redirecting.
        try:
            return await _stream_remote_file(storage, file_metadata, file_key)
        except FileNotFoundError:
            api_logger.warning(f"File not found in MinIO: file_key={file_key}")
            raise BusinessException(
                "File not found in storage", BizCode.NOT_FOUND, 404) from None
        except Exception as e:
            api_logger.error(f"Failed to stream file from MinIO: {e}")
            raise BusinessException(
                "Failed to retrieve file", BizCode.INTERNAL_ERROR, 500) from e
    else:
        # S3/OSS presigned URLs are public HTTPS; keep the 302 redirect.
        try:
            # 7 days is the maximum presigned expiration for most cloud providers.
            presigned_url = await storage_service.get_file_url(
                file_key, expires=604800, file_name=file_metadata.file_name)
            presigned_url = _match_scheme(request, presigned_url)
            return RedirectResponse(url=presigned_url, status_code=302)
        except Exception as e:
            api_logger.error(f"Failed to get presigned URL: {e}")
            raise BusinessException(
                "Failed to retrieve file", BizCode.INTERNAL_ERROR, 500) from e
