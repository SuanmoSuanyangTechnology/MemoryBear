"""Refresh the shared ``forget:candidates`` set after a merge.

Mirrors the API's ``sync_end_user_memory_count_from_neo4j`` side effect: the
candidate flag is decided from the active core-node count (Statement / Chunk /
ExtractedEntity) versus the tenant's ``end_user_memory_limit`` (free-plan
fallback 300). The merge itself only moves data; this keeps the forgetting
engine's candidate gate consistent with the target's new size.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping

import sqlalchemy as sa

from src.infrastructure.postgres import PostgresManager
from src.models.references import Workspace
from src.models.references.end_user_model import EndUser
from src.repositories.forgetting import ForgettingRepository

logger = logging.getLogger(__name__)

FORGET_CANDIDATES_KEY = "forget:candidates"
#: Free-plan fallback used by the API and the forgetting service.
DEFAULT_MEMORY_LIMIT = 300
_CORE_LABELS = ("Statement", "Chunk", "ExtractedEntity")

RefreshForgetCandidates = Callable[[uuid.UUID], Awaitable[None]]
LimitsProvider = Callable[[uuid.UUID], Awaitable[Mapping[str, object]]]


class ForgetCandidateRefresher:
    """Add or remove one end user from the forgetting candidate set."""

    def __init__(
        self,
        *,
        postgres: PostgresManager,
        neo4j: object,
        redis_manager: object,
        limits: LimitsProvider,
    ) -> None:
        self._postgres = postgres
        self._neo4j = neo4j
        self._redis_manager = redis_manager
        self._limits = limits

    async def __call__(self, end_user_id: uuid.UUID) -> None:
        tenant_id = await self._tenant_of(end_user_id)
        if tenant_id is None:
            return
        breakdown = await ForgettingRepository(
            self._postgres, self._neo4j  # type: ignore[arg-type]
        ).quota_breakdown(end_user_id)
        active = sum(int(breakdown.get(label, 0) or 0) for label in _CORE_LABELS)
        limits = await self._limits(tenant_id)
        limit = int(limits.get("end_user_memory_limit") or DEFAULT_MEMORY_LIMIT)
        client = await self._redis_manager.client()  # type: ignore[attr-defined]
        if active > limit:
            await client.sadd(FORGET_CANDIDATES_KEY, str(end_user_id))
        else:
            await client.srem(FORGET_CANDIDATES_KEY, str(end_user_id))

    async def _tenant_of(self, end_user_id: uuid.UUID) -> uuid.UUID | None:
        async with self._postgres.session() as session:
            return await session.scalar(
                sa.select(Workspace.tenant_id)
                .join(EndUser, EndUser.workspace_id == Workspace.id)
                .where(EndUser.id == end_user_id)
            )


__all__ = [
    "DEFAULT_MEMORY_LIMIT",
    "FORGET_CANDIDATES_KEY",
    "ForgetCandidateRefresher",
    "RefreshForgetCandidates",
]