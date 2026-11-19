"""Redis workspace circuit breaker for metadata migration."""

from __future__ import annotations

import logging
from collections.abc import Callable

from redis import Redis
from task_bridge.metadata_migration import metadata_migration_circuit_breaker_key

logger = logging.getLogger(__name__)


class MetadataMigrationCircuitBreaker:
    """Pause a workspace after a model-availability failure.

    Redis failures degrade open: a breaker outage must not block migration work.
    """

    def __init__(self, client_provider: Callable[[], Redis], ttl_seconds: int) -> None:
        self._client_provider = client_provider
        self._ttl_seconds = ttl_seconds

    def is_open(self, workspace_id: str) -> bool:
        try:
            return bool(self._client_provider().exists(
                metadata_migration_circuit_breaker_key(workspace_id)
            ))
        except Exception:
            logger.warning(
                "metadata migration circuit-breaker lookup failed workspace=%s",
                workspace_id,
                exc_info=True,
            )
            return False

    def open(self, workspace_id: str) -> None:
        try:
            self._client_provider().set(
                metadata_migration_circuit_breaker_key(workspace_id),
                "model_unavailable",
                ex=self._ttl_seconds,
            )
            logger.warning(
                "metadata migration circuit breaker opened workspace=%s ttl_seconds=%s",
                workspace_id,
                self._ttl_seconds,
            )
        except Exception:
            logger.warning(
                "metadata migration circuit-breaker open failed workspace=%s",
                workspace_id,
                exc_info=True,
            )
