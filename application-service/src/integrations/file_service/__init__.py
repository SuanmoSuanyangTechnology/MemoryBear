"""Client side of the independent file service (core/file-service)."""

from __future__ import annotations

from ...config import current_settings
from .client import FileBytes, FileServiceClient, FileUploadResult


def get_file_service_client() -> FileServiceClient:
    """Build the client from process settings.

    Unconfigured values fail closed on the first call instead of blocking
    startup; the file service is a runtime dependency of the upload paths only.
    """

    settings = current_settings()
    return FileServiceClient(
        base_url=settings.file_service_base_url,
        token=settings.file_service_internal_token.get_secret_value(),
    )


__all__ = [
    "FileBytes",
    "FileServiceClient",
    "FileUploadResult",
    "get_file_service_client",
]
