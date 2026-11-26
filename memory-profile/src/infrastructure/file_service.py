"""Async client for uploads and downloads through the standalone file service.

memory-profile no longer owns object storage or writes ``file_metadata``: the
``file-service`` owns both. An upload is forwarded to the token-guarded internal
API (``POST /internal/v1/files/upload``) with the shared
``X-File-Service-Token`` credential; ``X-Tenant-Id`` / ``X-Workspace-Id`` /
``X-Actor-Id`` carry the caller identity, exactly like the application service's
client.

The target enforces its own size limit and writes the ``file_metadata`` row. Its
success payload is the ``{file_id, file_key}`` pair the host upload response
needs.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

#: Upload on file-service's token-guarded internal API.
UPLOAD_PATH = "/internal/v1/files/upload"
CONTENT_PATH = "/internal/v1/files/{file_id}/content"
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _secret_value(value: object) -> str:
    """Read a plain string or a ``SecretStr`` without importing pydantic here."""

    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")


class FileServiceError(RuntimeError):
    """A forward failed; ``status_code`` is what the host boundary should return."""

    def __init__(self, detail: str, *, status_code: int = 500) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class FileServiceClient:
    """One file-service client per process."""

    def __init__(
        self,
        settings: ProfileSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._token = _secret_value(settings.file_service_internal_token)

    @property
    def enabled(self) -> bool:
        """Whether both the service URL and the shared token are configured."""

        return bool(self._settings.file_service_base_url and self._token)

    def _ensure_configured(self) -> None:
        if not self._settings.file_service_base_url:
            raise FileServiceError("File service is not configured", status_code=503)
        if not self._token:
            raise FileServiceError(
                "FILE_SERVICE_INTERNAL_TOKEN is not configured", status_code=503
            )

    def _headers(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID | None,
        actor_id: UUID,
        actor_kind: str,
    ) -> dict[str, str]:
        """Authenticate one request and carry the acting actor's identity.

        The actor is the API-key id for a keyed caller, the same value the host
        passed as ``x-user-id`` / ``x-api-key-id``; ``X-Actor-Id`` is what the
        file service records as the uploader.
        """

        headers = {
            "X-File-Service-Token": self._token,
            "X-Tenant-Id": str(tenant_id),
            "X-Actor-Id": str(actor_id),
        }
        if workspace_id is not None:
            headers["X-Workspace-Id"] = str(workspace_id)
        return headers

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.file_service_base_url.rstrip("/"),
            timeout=httpx.Timeout(
                connect=2.0,
                read=self._settings.file_service_read_timeout_seconds,
                write=self._settings.file_service_write_timeout_seconds,
                pool=None,
            ),
            transport=self._transport,
            trust_env=False,
        )

    async def download(
        self,
        file_id: UUID,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_id: UUID,
        actor_kind: str,
    ) -> bytes:
        self._ensure_configured()
        headers = self._headers(
            tenant_id=tenant_id, workspace_id=workspace_id, actor_id=actor_id,
            actor_kind=actor_kind,
        )
        try:
            async with self._http_client() as client:
                response = await client.get(CONTENT_PATH.format(file_id=file_id), headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise FileServiceError(
                f"File service returned HTTP {status}", status_code=404 if status == 404 else 502
            ) from exc
        except httpx.HTTPError as exc:
            raise FileServiceError("File service is unavailable", status_code=503) from exc
        return response.content

    async def upload(
        self,
        *,
        file: Any,
        tenant_id: UUID,
        workspace_id: UUID | None,
        actor_id: UUID,
        actor_kind: str,
    ) -> dict[str, str]:
        """Forward one upload and return ``{"file_id", "file_key"}``."""

        self._ensure_configured()
        content = await file.read()
        if not content:
            raise FileServiceError("The file is empty.", status_code=400)
        if len(content) > self._settings.max_file_size:
            raise FileServiceError(
                f"The file size exceeds the {self._settings.max_file_size} byte limit",
                status_code=413,
            )

        headers = self._headers(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            actor_kind=actor_kind,
        )
        filename = getattr(file, "filename", None) or "upload"
        content_type = getattr(file, "content_type", None) or DEFAULT_CONTENT_TYPE
        files = {"file": (filename, content, content_type)}
        try:
            async with self._http_client() as client:
                response = await client.post(UPLOAD_PATH, headers=headers, files=files)
        except httpx.HTTPError as exc:
            raise FileServiceError(
                "File service is unavailable", status_code=503
            ) from exc

        if response.status_code == 413:
            raise FileServiceError("The file is too large", status_code=413)
        if response.status_code != 200:
            raise FileServiceError(
                f"File service returned HTTP {response.status_code}", status_code=502
            )
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise FileServiceError(
                "File service returned an invalid response", status_code=502
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(
            data, dict
        ):
            raise FileServiceError(
                "File service returned an invalid response", status_code=502
            )
        file_id = str(data.get("file_id") or "")
        file_key = str(data.get("file_key") or "")
        if not file_id or not file_key:
            raise FileServiceError(
                "File service returned an invalid response", status_code=502
            )
        return {"file_id": file_id, "file_key": file_key}

    async def aclose(self) -> None:
        """No persistent client: each request owns its short-lived connection."""

        return None


__all__ = ["DEFAULT_CONTENT_TYPE", "UPLOAD_PATH", "FileServiceClient", "FileServiceError"]
