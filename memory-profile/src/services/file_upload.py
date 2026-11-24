"""Upload for ``POST /v1/memory/files/upload``, delegated to file-service.

``file-service`` owns the storage backend and the ``file_metadata`` table, so
this service only maps the internal client's failure vocabulary onto the host's
HTTP shape.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.infrastructure.file_service import FileServiceClient, FileServiceError


class FileUploadRejected(Exception):
    """A host ``HTTPException`` shape: status plus raw detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FileUploadService:
    """Forward one upload with the acting actor's internal identity."""

    def __init__(self, *, client: FileServiceClient) -> None:
        self._client = client

    async def upload(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        actor_id: uuid.UUID,
        actor_kind: str,
        file: Any,
    ) -> dict[str, str]:
        try:
            return await self._client.upload(
                file=file,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                actor_kind=actor_kind,
            )
        except FileServiceError as exc:
            raise FileUploadRejected(exc.status_code, exc.detail) from exc


__all__ = ["FileUploadRejected", "FileUploadService"]