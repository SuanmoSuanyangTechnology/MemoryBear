"""Memory-engine quota admission.

Semantics are the monolith's ``_check_quota`` for ``memory_engine_quota``: a
missing key skips the check, and usage at or above the limit is denied. The
count is ``memory_config ⋈ workspaces``, scoped to one workspace when a
workspace id is given, otherwise to every workspace of the tenant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.references import MemoryConfigRow, Workspace
from src.services.quota.entitlements import QuotaEntitlements
from src.services.quota.errors import QuotaDenied

logger = logging.getLogger(__name__)

QUOTA_TYPE = "memory_engine_quota"
#: Resource key the legacy ``QuotaExceededError`` mapped to ``quota_resources.memory_engine``.
RESOURCE_KEY = "memory_engine"
RESOURCE_NAME = "memory_engine"


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    allowed: bool
    usage: int | float | None = None
    limit: int | float | None = None
    resource: str = RESOURCE_NAME

    def require(self) -> AdmissionOutcome:
        if not self.allowed:
            raise QuotaDenied(
                resource=self.resource,
                current_usage=self.usage or 0,
                quota_limit=self.limit or 0,
                quota_type=QUOTA_TYPE,
            )
        return self


async def count_memory_engines(
    session: AsyncSession, tenant_id: UUID, workspace_id: UUID | None = None
) -> int:
    """Count configuration rows, workspace-scoped when an id is supplied."""

    statement = (
        select(func.count())
        .select_from(MemoryConfigRow)
        .join(Workspace, MemoryConfigRow.workspace_id == Workspace.id)
    )
    if workspace_id:
        statement = statement.where(MemoryConfigRow.workspace_id == workspace_id)
    else:
        statement = statement.where(Workspace.tenant_id == tenant_id)
    return int((await session.scalar(statement)) or 0)


async def admit_memory_engine(
    session: AsyncSession,
    tenant_id: UUID,
    workspace_id: UUID,
    entitlements: QuotaEntitlements,
) -> AdmissionOutcome:
    limits = await entitlements.effective_limits(tenant_id)
    limit = limits.get(QUOTA_TYPE)
    if limit is None:
        logger.warning("quota configuration lacks %s; skipping admission", QUOTA_TYPE)
        return AdmissionOutcome(allowed=True)
    usage = await count_memory_engines(session, tenant_id, workspace_id)
    return AdmissionOutcome(
        allowed=usage < limit,
        usage=usage,
        limit=limit,
        resource=RESOURCE_NAME,
    )


async def check_memory_engine_quota(
    session: AsyncSession,
    tenant_id: UUID,
    workspace_id: UUID,
    entitlements: QuotaEntitlements,
) -> AdmissionOutcome:
    """Admission that raises :class:`QuotaDenied` on rejection."""

    return (await admit_memory_engine(session, tenant_id, workspace_id, entitlements)).require()
