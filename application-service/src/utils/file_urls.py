"""Permanent file URL builder (host parity with ``file_content_service``)."""

from __future__ import annotations

import uuid

from ..config import current_settings

__all__ = ["build_permanent_file_url"]

# Path marker of this deployment's locally served files (host parity).
_PERMANENT_PATH_MARKER = "/storage/permanent/"


def build_permanent_file_url(file_id: uuid.UUID | str) -> str:
    """Permanent download URL minted by this service (internal handle only)."""

    base = current_settings().file_local_server_url.rstrip("/")
    return f"{base}{_PERMANENT_PATH_MARKER}{file_id}"
