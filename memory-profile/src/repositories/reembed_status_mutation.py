"""Write access for manual re-embed retries (API-owned job tables).

Port of the API's ``memory_reembed_service.retry_job_users`` behind the four
``workspace_reembed/.../retry`` endpoints. The transaction is the same and in the
same order: lock the workspace row, read the job under that lock, refuse a job
whose target embedding model is no longer the workspace's current one, reopen a
``failed`` job (conditional write), reset the terminal-failed end-user rows to
``pending``, commit.

Redis claim clearing and re-dispatch stay outside this module: they must happen
after the commit, because a dispatch failure must never roll back stored state
(the periodic reconciler re-dispatches ``pending`` rows anyway).

DDL stays owned by the API's Alembic chain; this module only writes rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.postgres import PostgresManager
from src.models.references import (
    MemoryReembedJobRow,
    MemoryReembedJobUserRow,
    Workspace,
)
from src.repositories.reembed_status import (
    DISPLAY_FAILED,
    USER_STATUS_PENDING,
    display_status_condition,
)

#: Stored job statuses this module transitions between (API vocabulary).
JOB_STATUS_FAILED = "failed"
JOB_STATUS_RUNNING = "running"

#: One manual retry requeues at most this many rows (API contract).
REEMBED_RETRY_BATCH_LIMIT = 500

#: Outcome kinds; the service layer maps them to payloads or domain errors.
NOT_FOUND = "not_found"
MODEL_CONFLICT = "model_conflict"
USER_NOT_FAILED = "user_not_failed"
NOTHING_RETRIED = "nothing_retried"
RETRIED = "retried"


@dataclass(slots=True)
class RetryWriteOutcome:
    """What the write transaction decided, plus what to dispatch afterwards."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    #: ``True`` when a zero-row job was reopened and its driver must run again.
    dispatch_job: bool = False


class ReembedRetryMutationRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def retry(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        end_user_ids: list[str] | None,
        max_attempts: int,
        stale_before: datetime,
    ) -> RetryWriteOutcome:
        """Reopen the job and requeue its terminal-failed rows, in one transaction.

        ``end_user_ids is None`` means "every terminal-failed row of this job"
        (the one-click variants); a named user with no terminal-failed row is a
        conflict, not an empty success.
        """

        async with self.postgres.session() as session:
            workspace = (
                await session.execute(
                    select(Workspace.id, Workspace.embedding)
                    .where(Workspace.id == workspace_id)
                    .with_for_update()
                )
            ).mappings().first()
            if workspace is None:
                return RetryWriteOutcome(NOT_FOUND)

            job = (
                await session.execute(
                    select(MemoryReembedJobRow).where(
                        MemoryReembedJobRow.id == job_id
                    )
                )
            ).scalars().first()
            if job is None or job.workspace_id != workspace_id:
                return RetryWriteOutcome(NOT_FOUND)

            # The same invariant the orchestrator enforces: retrying a job whose
            # target model is stale would write vectors the read path no longer
            # uses, which is exactly what the whole rebuild exists to prevent.
            current_embedding = (
                None if workspace["embedding"] is None else str(workspace["embedding"])
            )
            if current_embedding != str(job.new_embedding_config_id):
                return RetryWriteOutcome(MODEL_CONFLICT)

            users = MemoryReembedJobUserRow
            failed = display_status_condition(
                DISPLAY_FAILED,
                users,
                max_attempts=max_attempts,
                stale_before=stale_before,
            )
            query = select(users.id, users.end_user_id).where(
                users.job_id == job_id, failed
            )
            if end_user_ids is not None:
                query = query.where(users.end_user_id.in_(end_user_ids))
            rows = (
                await session.execute(query.limit(REEMBED_RETRY_BATCH_LIMIT))
            ).all()

            if end_user_ids is not None and not rows:
                return RetryWriteOutcome(USER_NOT_FAILED)

            if not rows:
                # Zero rows but the job is ``failed``: the pre-flight check
                # rejected the job before any row existed. The retry belongs to
                # the job, not to a row; reopen it and let ``run_job`` re-probe.
                reopened = await _reopen_failed_job(session, job)
                if not reopened:
                    # Someone else moved the job out of ``failed`` first.
                    return RetryWriteOutcome(
                        NOTHING_RETRIED,
                        _payload(job, retried=0, reopened=False),
                    )
                await session.commit()
                return RetryWriteOutcome(
                    NOTHING_RETRIED,
                    _payload(job, retried=0, reopened=True),
                    dispatch_job=True,
                )

            reopened = await _reopen_failed_job(session, job)

            flipped = set(
                (
                    await session.execute(
                        update(users)
                        .where(users.id.in_([row.id for row in rows]), failed)
                        .values(
                            status=USER_STATUS_PENDING,
                            # Manual intervention restarts the budget and drops
                            # the stale error the list would otherwise show.
                            attempts=0,
                            last_error=None,
                        )
                        .returning(users.id)
                        .execution_options(synchronize_session=False)
                    )
                ).scalars()
            )
            retried_ids = [str(row.end_user_id) for row in rows if row.id in flipped]
            await session.commit()
            return RetryWriteOutcome(
                RETRIED,
                _payload(
                    job,
                    retried=len(retried_ids),
                    reopened=reopened,
                    end_user_ids=retried_ids,
                ),
            )


async def _reopen_failed_job(session: AsyncSession, job: MemoryReembedJobRow) -> bool:
    """Reopen a ``failed`` job; return whether this call was the one that did.

    Must be a conditional write (CAS), never an attribute assignment: ``job`` was
    read under the workspace lock, but a concurrent retry or a finishing worker
    may still have moved the row. The predicate makes "hit at most once" a
    database guarantee, and a miss must not overwrite a ``succeeded`` job back
    to ``running``.
    """

    updated = await session.execute(
        update(MemoryReembedJobRow)
        .where(
            MemoryReembedJobRow.id == job.id,
            MemoryReembedJobRow.status == JOB_STATUS_FAILED,
        )
        .values(
            status=JOB_STATUS_RUNNING,
            finished_at=None,
            # ``finalize_job_if_complete`` keeps an existing error on success, so
            # leaving the old text would report a stale failure afterwards.
            error=None,
        )
        .execution_options(synchronize_session=False)
    )
    # Report the database's truth, not the possibly stale identity-map value.
    await session.refresh(job)
    return bool(updated.rowcount)


def _payload(
    job: MemoryReembedJobRow,
    *,
    retried: int,
    reopened: bool,
    end_user_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "retried": retried,
        "reopened": reopened,
        "job_status": str(job.status or ""),
        "end_user_ids": list(end_user_ids or []),
    }


__all__ = [
    "MODEL_CONFLICT",
    "NOT_FOUND",
    "NOTHING_RETRIED",
    "REEMBED_RETRY_BATCH_LIMIT",
    "RETRIED",
    "RetryWriteOutcome",
    "USER_NOT_FAILED",
    "ReembedRetryMutationRepository",
]