"""Scene-name lookup adapter over the optional enterprise service.

The enterprise manager is transport only. This adapter owns the best-effort
policy the projection depends on: an outage must not be mistaken for "the scene
was deleted", so an unavailable lookup returns ``None`` and the caller keeps the
stored keys instead of dropping them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from uuid import UUID

from src.domain.shared.ids import TenantId
from src.infrastructure.memory_enterprise import MemoryEnterpriseManager

logger = logging.getLogger(__name__)


class MemoryEnterpriseSceneNames:
    """Implement ``SceneNameLookup`` against the enterprise service."""

    def __init__(self, manager: MemoryEnterpriseManager) -> None:
        self._manager = manager

    def resolve(
        self, scene_type_ids: Sequence[UUID], tenant_id: TenantId
    ) -> Mapping[str, str] | None:
        if not scene_type_ids:
            return {}
        if not self._manager.enabled:
            # No enterprise service is configured: the stored uuid scenes cannot
            # exist in this deployment either, so "no names" is authoritative.
            return {}
        try:
            return self._manager.scene_names(scene_type_ids, tenant_id)
        except Exception as exc:
            logger.warning(
                "memory-enterprise scene names unavailable error=%s",
                type(exc).__name__,
                exc_info=True,
                extra={
                    "event": "enterprise_scene_names_failed",
                    "exception_type": type(exc).__name__,
                },
            )
            return None
