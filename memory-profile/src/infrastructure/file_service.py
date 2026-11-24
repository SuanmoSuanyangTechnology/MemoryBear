"""Async client for the standalone file service (upload delegation).

memory-profile no longer owns object storage or writes ``file_metadata``: the
``file-service`` owns both. An upload is forwarded with an internal JWT
(RS256, ``aud=file-service``) signed by the same issuer key gateway and
identity-service use, so file-service verifies it through the JWKS identity
publishes. The matching identity headers are sent as well: the service's
``direct`` auth mode trusts them, and its ``gateway`` mode rejects a header
that disagrees with the token claims.

The target is the storage upload route (``POST /internal/v1/storage/files``),
which streams the body, enforces its own size limit and writes the
``file_metadata`` row. Its success payload is the ``{file_id, file_key}`` pair
the host upload response needs.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from auth_sdk.schema import UserContext
from auth_sdk.token import LocalTokenIssuer

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

#: Upload on file-service; the bearer token is accepted by gateway auth mode.
UPLOAD_PATH = "/internal/v1/storage/files"
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class FileServiceError(RuntimeError):
    """A forward failed; ``status_code`` is what the host boundary should return."""

    def __init__(self, detail: str, *, status_code: int = 500) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class FileServiceClient:
    """One file-service uploader per process."""

    def __init__(
        self,
        settings: ProfileSettings,
        *,
        issuer: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._issuer = issuer
        if self._issuer is None:
            key = getattr(settings, "internal_issuer_private_key", None)
            private_key = (
                key.get_secret_value()
                if hasattr(key, "get_secret_value")
                else str(key or "")
            )
            if private_key:
                self._issuer = LocalTokenIssuer(
                    private_key=private_key,
                    kid=settings.internal_issuer_kid,
                    ttl=settings.internal_token_ttl_seconds,
                    issuer=settings.service_name,
                )

    @property
    def enabled(self) -> bool:
        """Whether both the service URL and the issuer key are configured."""

        return bool(self._settings.file_service_base_url and self._issuer is not None)

    def _ensure_configured(self) -> None:
        if not self._settings.file_service_base_url:
            raise FileServiceError("File service is not configured", status_code=503)
        if self._issuer is None:
            raise FileServiceError(
                "Internal issuer key is not configured", status_code=503
            )

    def _headers(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID | None,
        actor_id: UUID,
        actor_kind: str,
    ) -> dict[str, str]:
        """Sign one request for the acting actor and carry the same identity headers.

        The token ``sub`` is the actor this request acts for (the API-key id for
        a keyed caller, the same value the host passed as ``x-user-id`` /
        ``x-api-key-id``). ``api_key_id`` stays a separate claim so downstream
        attribution can tell the credential from the person.
        """

        identity = UserContext(
            user_id=str(actor_id),
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id) if workspace_id else "",
            auth_type="api_key" if actor_kind == "apikey" else None,
            api_key_id=str(actor_id) if actor_kind == "apikey" else None,
        )
        token = self._issuer.issue_internal_token(
            identity, self._settings.file_service_audience
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": str(tenant_id),
            "X-User-Id": str(actor_id),
        }
        if workspace_id is not None:
            headers["X-Workspace-Id"] = str(workspace_id)
        return headers

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
        timeout = httpx.Timeout(
            connect=2.0,
            read=self._settings.file_service_read_timeout_seconds,
            write=self._settings.file_service_write_timeout_seconds,
            pool=None,
        )

        try:
            async with httpx.AsyncClient(
                base_url=self._settings.file_service_base_url.rstrip("/"),
                timeout=timeout,
                transport=self._transport,
                trust_env=False,  # never send file bytes through an env proxy
            ) as client:
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
        """No persistent client: each upload owns its short-lived connection."""

        return None


__all__ = ["DEFAULT_CONTENT_TYPE", "UPLOAD_PATH", "FileServiceClient", "FileServiceError"]