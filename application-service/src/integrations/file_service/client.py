"""HTTP client for the independent file service (``/internal/v1/files``).

The service wraps every answer in the project envelope ``{"code", "msg", "data",
"error", "time"}``; success is ``code == 0``. The upload is non-idempotent and is
never retried. The client fails closed when unconfigured, and every httpx
transport failure is translated to a business error at this boundary so no
transport exception type escapes the integration.

Callers that store the bytes themselves (TTS, sandbox artifacts) use the
lifecycle endpoints: ``create_pending`` registers the record and returns the
server-generated file id/key, then ``complete``/``fail`` close it out after the
local storage write.

Streaming note: the pinned httpx (0.28) multipart encoder only accepts sync
file objects, so ``upload_stream`` hand-builds the multipart frame and hands
httpx an async byte stream; the body is never buffered as a whole.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import UploadFile

from ...errors import BizCode, BusinessException

logger = logging.getLogger(__name__)

_UPLOAD_PATH = "/internal/v1/files/upload"
_PENDING_PATH = "/internal/v1/files/pending"
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_UNAVAILABLE_MSG = "File service is unavailable"
_INVALID_RESPONSE_MSG = "File service returned an invalid response"

# Same filename escaping as the httpx multipart encoder (RFC 7578 / browser
# form behavior): quotes, backslashes and control characters are percent-encoded.
_FORM_ENCODING = {'"': "%22", "\\": "\\\\"}
_FORM_ENCODING.update({chr(c): f"%{c:02X}" for c in range(0x20) if c != 0x1B})


@dataclass(frozen=True)
class FileUploadResult:
    """One stored file as reported by the file-service upload endpoint."""

    file_id: str
    file_key: str
    file_name: str
    file_size: int
    content_type: str | None
    status: str


@dataclass(frozen=True)
class FileRecord:
    """One file record as reported by the internal lifecycle endpoints."""

    file_id: str
    file_key: str
    file_name: str
    file_ext: str
    file_size: int
    content_type: str | None
    status: str


def _format_form_param(name: str, value: str) -> str:
    encoded = "".join(_FORM_ENCODING.get(char, char) for char in value)
    return f'{name}="{encoded}"'


def _multipart_head(
    boundary: str,
    field_name: str,
    file_name: str,
    content_type: str | None,
) -> bytes:
    lines = [
        f"--{boundary}\r\n",
        "Content-Disposition: form-data; "
        f"{_format_form_param('name', field_name)}; "
        f"{_format_form_param('filename', file_name)}\r\n",
    ]
    if content_type:
        lines.append(f"Content-Type: {content_type}\r\n")
    lines.append("\r\n")
    return "".join(lines).encode()


async def _iter_multipart_file(
    file: UploadFile,
    *,
    boundary: str,
    field_name: str = "file",
) -> AsyncIterator[bytes]:
    yield _multipart_head(
        boundary,
        field_name,
        file.filename or "upload",
        file.content_type,
    )
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk
    yield f"\r\n--{boundary}--\r\n".encode()


def _envelope_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("msg") or payload.get("error") or "")


def _raise_for_error_status(response: httpx.Response, fallback_msg: str) -> None:
    if response.status_code == 413:
        raise BusinessException(
            _envelope_message(response) or "The file is too large",
            BizCode.FILE_TOO_LARGE,
        )
    if response.status_code >= 500:
        raise BusinessException(
            _envelope_message(response) or _UNAVAILABLE_MSG,
            BizCode.SERVICE_UNAVAILABLE,
        )
    if not response.is_success:
        raise BusinessException(
            _envelope_message(response) or fallback_msg, BizCode.BAD_REQUEST
        )


def _parse_envelope(response: httpx.Response, fallback_msg: str) -> dict[str, Any]:
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise BusinessException(
            _INVALID_RESPONSE_MSG, BizCode.SERVICE_UNAVAILABLE, cause=exc
        ) from exc
    if not isinstance(payload, dict):
        raise BusinessException(_INVALID_RESPONSE_MSG, BizCode.SERVICE_UNAVAILABLE)
    if payload.get("code") != 0:
        raise BusinessException(
            str(payload.get("msg") or fallback_msg), BizCode.BAD_REQUEST
        )
    return payload


def _parse_upload_response(response: httpx.Response) -> FileUploadResult:
    _raise_for_error_status(response, "File upload failed")
    payload = _parse_envelope(response, "File upload failed")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise BusinessException(_INVALID_RESPONSE_MSG, BizCode.SERVICE_UNAVAILABLE)
    try:
        return FileUploadResult(
            file_id=str(data["file_id"]),
            file_key=str(data["file_key"]),
            file_name=str(data["file_name"]),
            file_size=int(data["file_size"]),
            content_type=data.get("content_type"),
            status=str(data["status"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BusinessException(
            _INVALID_RESPONSE_MSG, BizCode.SERVICE_UNAVAILABLE, cause=exc
        ) from exc


def _parse_record_response(
    response: httpx.Response, fallback_msg: str
) -> FileRecord:
    _raise_for_error_status(response, fallback_msg)
    payload = _parse_envelope(response, fallback_msg)

    data = payload.get("data")
    if not isinstance(data, dict):
        raise BusinessException(_INVALID_RESPONSE_MSG, BizCode.SERVICE_UNAVAILABLE)
    try:
        return FileRecord(
            file_id=str(data["file_id"]),
            file_key=str(data["file_key"]),
            file_name=str(data["file_name"]),
            file_ext=str(data["file_ext"]),
            file_size=int(data["file_size"]),
            content_type=data.get("content_type"),
            status=str(data["status"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BusinessException(
            _INVALID_RESPONSE_MSG, BizCode.SERVICE_UNAVAILABLE, cause=exc
        ) from exc


class FileServiceClient:
    """Client of the file-service internal upload API.

    ``transport`` is a test seam (``httpx.MockTransport``); production callers
    use the factory in ``src.integrations.file_service``.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._token = token or ""
        self._transport = transport

    def _ensure_configured(self) -> None:
        if not self._base_url or not self._token:
            raise BusinessException(
                "File service is not configured", BizCode.SERVICE_UNAVAILABLE
            )

    def _headers(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        actor_id: uuid.UUID | str | None,
    ) -> dict[str, str]:
        headers = {
            "X-File-Service-Token": self._token,
            "X-Tenant-Id": str(tenant_id),
        }
        if workspace_id is not None:
            headers["X-Workspace-Id"] = str(workspace_id)
        if actor_id is not None:
            headers["X-Actor-Id"] = str(actor_id)
        return headers

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            # httpx requires all four phases once any is set; pool keeps its
            # default (no timeout) while uploads get a long write budget.
            timeout=httpx.Timeout(connect=2.0, read=30.0, write=300.0, pool=None),
            transport=self._transport,
            trust_env=False,  # never send file bytes through an env proxy
        )

    async def upload(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        actor_id: uuid.UUID | str | None,
        file_name: str,
        content_type: str | None,
        content: bytes,
    ) -> FileUploadResult:
        """Upload one in-memory payload.

        For the shared-link surface, where the monolith contract reads the
        whole body up front. Chat attachments use ``upload_stream``.
        """

        self._ensure_configured()
        files = {
            "file": (
                file_name or "upload",
                content,
                content_type or "application/octet-stream",
            )
        }
        async with self._new_client() as client:
            return await self._post(
                client,
                headers=self._headers(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                ),
                files=files,
            )

    async def upload_stream(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        actor_id: uuid.UUID | str | None,
        file: UploadFile,
    ) -> FileUploadResult:
        """Stream one ``UploadFile`` without ever holding it in memory whole."""

        self._ensure_configured()
        boundary = uuid.uuid4().hex
        headers = self._headers(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        async with self._new_client() as client:
            return await self._post(
                client,
                headers=headers,
                content=_iter_multipart_file(file, boundary=boundary),
            )

    async def create_pending(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        actor_id: uuid.UUID | str | None,
        file_name: str,
        file_ext: str | None = None,
        content_type: str | None = None,
        file_size: int | None = None,
    ) -> FileRecord:
        """Register a pending record the caller will fill with bytes itself."""

        self._ensure_configured()
        body: dict[str, Any] = {"file_name": file_name}
        if file_ext is not None:
            body["file_ext"] = file_ext
        if content_type is not None:
            body["content_type"] = content_type
        if file_size is not None:
            body["file_size"] = file_size

        async with self._new_client() as client:
            try:
                response = await client.post(
                    _PENDING_PATH,
                    headers=self._headers(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                    ),
                    json=body,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning(
                    f"File service pending create failed: {type(exc).__name__}"
                )
                raise BusinessException(
                    _UNAVAILABLE_MSG, BizCode.SERVICE_UNAVAILABLE, cause=exc
                ) from exc
        return _parse_record_response(response, "File record creation failed")

    async def complete(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        actor_id: uuid.UUID | str | None,
        file_id: str | uuid.UUID,
        size: int,
    ) -> FileRecord:
        """Mark the record completed after the caller wrote ``size`` bytes."""

        self._ensure_configured()
        async with self._new_client() as client:
            return await self._patch(
                client,
                path=f"/internal/v1/files/{file_id}/complete",
                headers=self._headers(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                ),
                json_body={"file_size": size},
            )

    async def fail(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        actor_id: uuid.UUID | str | None,
        file_id: str | uuid.UUID,
    ) -> FileRecord:
        """Mark the record failed; the caller keeps its own object cleanup."""

        self._ensure_configured()
        async with self._new_client() as client:
            return await self._patch(
                client,
                path=f"/internal/v1/files/{file_id}/failed",
                headers=self._headers(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                ),
            )

    async def _post(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        files: dict[str, tuple[str, bytes, str]] | None = None,
        content: AsyncIterator[bytes] | None = None,
    ) -> FileUploadResult:
        try:
            response = await client.post(
                _UPLOAD_PATH, headers=headers, files=files, content=content
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(f"File service upload failed: {type(exc).__name__}")
            raise BusinessException(
                _UNAVAILABLE_MSG, BizCode.SERVICE_UNAVAILABLE, cause=exc
            ) from exc
        return _parse_upload_response(response)

    async def _patch(
        self,
        client: httpx.AsyncClient,
        *,
        path: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
    ) -> FileRecord:
        try:
            response = await client.patch(path, headers=headers, json=json_body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(
                f"File service status update failed: {type(exc).__name__}"
            )
            raise BusinessException(
                _UNAVAILABLE_MSG, BizCode.SERVICE_UNAVAILABLE, cause=exc
            ) from exc
        return _parse_record_response(response, "File status update failed")


__all__ = ["FileRecord", "FileServiceClient", "FileUploadResult"]
