"""Handler for the migrated file upload endpoint.

``file-service`` owns the storage backend and the ``file_metadata`` table; this
handler validates nothing storage-specific and only maps the client's failures
onto the host's HTTP envelope.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from src.common.responses import http_error_response, success
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


class FileHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.runtime = runtime

    async def upload(
        self,
        request: Request,
        context: MemoryPrincipal,
        file: Any,
    ) -> Any:
        """Forward the host upload to the standalone file service."""

        from src.services.file_upload import FileUploadRejected, FileUploadService

        service = FileUploadService(client=self.runtime.file_service)
        try:
            data = await service.upload(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_id=context.actor_id,
                actor_kind=context.actor_kind,
                file=file,
            )
        except FileUploadRejected as exc:
            return http_error_response(request, exc.status_code, exc.detail)
        except Exception as exc:  # noqa: BLE001 - boundary maps to HTTP 500
            logger.exception("file upload failed")
            return http_error_response(request, 500, str(exc))
        return success(data=data, msg="File upload successful")


__all__ = ["FileHandler"]