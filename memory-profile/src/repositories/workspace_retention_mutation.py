"""Write access for the workspace retention policy (API-owned ``workspaces``).

One UPDATE of ``workspaces.retention_days``, preceded — when the new period is
shorter than the old one — by a batched UPDATE that stamps
``end_users.grace_period_until`` on identities the shorter period already
expired. Both statements commit in one transaction, exactly like the API's
``WorkspaceRepository.update_workspace_retention_days``: a policy change and the
grace deadlines it implies must never be visible separately.

DDL stays owned by the API's Alembic chain; this module only writes rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.timing import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, Workspace
from src.repositories.workspace_retention import TEMPORARY_IDENTITY_STATUS

#: Days between "expired under the new policy" and actual deletion (API contract).
RETENTION_GRACE_PERIOD_DAYS = 3
#: Rows stamped per batch; bounds the transaction and the lock window.
RETENTION_GRACE_PERIOD_UPDATE_BATCH_SIZE = 1_000


class WorkspaceRetentionMutationRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def update(self, workspace_id: UUID, retention_days: int | None) -> int | None:
        """Persist a new retention period and protect newly expired identities.

        Mirrors the API: the workspace row is locked first, the grace-period sweep
        only runs when the period shrinks (a longer period expires nobody), and the
        returned value is the stored one.
        """

        reference_time = utcnow_naive()
        async with self.postgres.session() as session:
            workspace = (
                (
                    await session.execute(
                        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
                    )
                )
                .scalars()
                .first()
            )
            if workspace is None:
                raise ValueError(f"Workspace not found: {workspace_id}")

            old_retention_days = workspace.retention_days
            if retention_days is not None and (
                old_retention_days is None or retention_days < old_retention_days
            ):
                await _mark_grace_period(
                    session,
                    workspace_id=workspace_id,
                    retention_days=retention_days,
                    reference_time=reference_time,
                )

            workspace.retention_days = retention_days
            await session.commit()
            return workspace.retention_days


async def _mark_grace_period(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    retention_days: int,
    reference_time: datetime,
) -> int:
    """Stamp ``grace_period_until`` on identities the new period expires.

    Keyset batched on ``(write_time, id)``: the same shape the API uses, so a
    workspace with millions of expired identities is updated in bounded
    transactions instead of one long-running UPDATE.
    """

    cutoff_time = reference_time - timedelta(days=retention_days)
    grace_period_until = reference_time + timedelta(days=RETENTION_GRACE_PERIOD_DAYS)
    expired_conditions = [
        EndUser.workspace_id == workspace_id,
        EndUser.is_active.is_(True),
        EndUser.identity_status == TEMPORARY_IDENTITY_STATUS,
        EndUser.write_time.is_not(None),
        EndUser.write_time < cutoff_time,
    ]

    affected = 0
    last_write_time: datetime | None = None
    last_end_user_id: UUID | None = None
    while True:
        statement = select(EndUser.id, EndUser.write_time).where(*expired_conditions)
        if last_write_time is not None and last_end_user_id is not None:
            statement = statement.where(
                sa.tuple_(EndUser.write_time, EndUser.id)
                > sa.tuple_(last_write_time, last_end_user_id)
            )
        candidates = (
            await session.execute(
                statement.order_by(EndUser.write_time.asc(), EndUser.id.asc()).limit(
                    RETENTION_GRACE_PERIOD_UPDATE_BATCH_SIZE
                )
            )
        ).all()
        if not candidates:
            break

        candidate_ids = [candidate[0] for candidate in candidates]
        result = await session.execute(
            update(EndUser)
            .where(EndUser.id.in_(candidate_ids), *expired_conditions)
            .values(grace_period_until=grace_period_until)
        )
        affected += max(result.rowcount or 0, 0)
        last_write_time = candidates[-1][1]
        last_end_user_id = candidates[-1][0]

        if len(candidates) < RETENTION_GRACE_PERIOD_UPDATE_BATCH_SIZE:
            break
    return affected