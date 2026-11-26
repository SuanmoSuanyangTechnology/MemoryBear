"""Client of ops-service internal home-page face (version card)."""

from __future__ import annotations

from ...config import current_settings
from .client import OpsServiceClient


def get_ops_service_client() -> OpsServiceClient:
    settings = current_settings()
    return OpsServiceClient(
        settings.ops_service_base_url,
        connect_timeout_seconds=settings.ops_service_connect_timeout_seconds,
        read_timeout_seconds=settings.ops_service_read_timeout_seconds,
    )


__all__ = ["OpsServiceClient", "get_ops_service_client"]
