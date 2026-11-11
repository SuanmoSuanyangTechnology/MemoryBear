"""Resolve a tenant's effective memory limits from its sources.

Resolution order mirrors the monolith: an active enterprise subscription
supplies the plan snapshot, otherwise (and whenever the enterprise service is
unknown or unreachable) the community free plan applies; resource-pack grants
are then added on top either way.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from src.config import ProfileSettings
from src.infrastructure.memory_enterprise import MemoryEnterpriseManager
from src.services.quota.defaults import MEMORY_LIMIT_KEYS, free_plan_limits

logger = logging.getLogger(__name__)


class QuotaEntitlements:
    """Resolve the three memory limits for one tenant."""

    def __init__(
        self,
        settings: ProfileSettings,
        enterprise: MemoryEnterpriseManager,
    ) -> None:
        self._settings = settings
        self._enterprise = enterprise

    @property
    def free_limits(self) -> dict[str, int]:
        return free_plan_limits(self._settings)

    async def effective_limits(
        self, tenant_id: UUID, *, fallback_on_error: bool = True
    ) -> dict[str, int | float]:
        """Resolve plan and resource-pack limits with the caller's fallback policy."""

        free = self.free_limits
        if not self._enterprise.enabled:
            return free
        try:
            data = await self._enterprise.memory_limits(tenant_id)
        except Exception:
            if not fallback_on_error:
                raise
            # A quota lookup must not take down a write path, and an unavailable
            # enterprise service is indistinguishable from "no subscription" to
            # the caller: both answer with the community free plan.
            logger.exception(
                "memory-enterprise quota unavailable tenant=%s; using free plan",
                tenant_id,
            )
            return free

        base = data.get("plan_quota") if data.get("has_subscription") else free
        overlay = data.get("resource_pack_quota")
        return _merge(
            base if isinstance(base, dict) else free,
            overlay if isinstance(overlay, dict) else {},
        )


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, int | float]:
    """Keep the three known keys and add the overlay on top of the base."""

    merged: dict[str, Any] = {key: base[key] for key in MEMORY_LIMIT_KEYS if key in base}
    for key in MEMORY_LIMIT_KEYS:
        if key in overlay:
            merged[key] = (merged.get(key) or 0) + overlay[key]
    return merged
