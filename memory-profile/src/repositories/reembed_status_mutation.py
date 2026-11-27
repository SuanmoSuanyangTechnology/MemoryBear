"""Write access for the re-embed job tables (API-owned rows).

Two write paths live here:

* job creation on behalf of the workspace service (``POST
  /workspace_reembed/jobs``): lock the workspace row, refuse a second active job,
  require the job's target model to already be the workspace's current embedding
  model, insert a ``pending`` row, commit;
* manual retries (the four ``workspace_reembed/.../retry`` endpoints), ported
  from the API's ``memory_reembed_service.retry_job_users``: lock the workspace
  row, read the job under that lock, refuse a job whose target embedding model is
  no longer the workspace's current one, reopen a ``failed`` job (conditional
  write), reset the terminal-failed end-user rows to ``pending``, commit.

Dispatch and Redis claim clearing stay outside this module: they must happen
after the commit, because a dispatch failure must never roll back stored state
(the periodic reconciler re-dispatches ``pending`` jobs/rows anyway).

DDL stays owned by the API's Alembic chain; this module only writes rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.timing import utcnow_naive
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
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
ACTIVE_JOB_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_RUNNING)

#: One manual retry requeues at most this many rows (API contract).
REEMBED_RETRY_BATCH_LIMIT = 500

#: Outcome kinds; the service layer maps them to payloads or domain errors.
NOT_FOUND = "not_found"
MODEL_CONFLICT = "model_conflict"
USER_NOT_FAILED = "user_not_failed"
NOTHING_RETRIED = "nothing_retried"
RETRIED = "retried"

#: Job-creation outcome kinds.
WORKSPACE_NOT_FOUND = "workspace_not_found"
ACTIVE_JOB_EXISTS = "active_job_exists"
MODEL_NOT_CURRENT = "model_not_current"
CREATED = "created"


@dataclass(slots=True)
class RetryWriteOutcome:
    """What the write transaction decided, plus what to dispatch afterwards."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    #: ``True`` when a zero-row job was reopened and its driver must run again.
    dispatch_job: bool = False


@dataclass(slots=True)
class JobCreateOutcome:
    """What the job-creation transaction decided."""

    kind: str
    job_id: str | None = None


class ReembedRetryMutationRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def active_job_id(self, workspace_id: UUID) -> str | None:
        """The workspace's in-flight job id, if any (``reembed_job_id`` parity).

        The API back-fills ``reembed_job_id`` with "the job in flight after this
        request", not with "the job this request created": switching only the
        llm slot while a rebuild still runs must return that rebuild's id.
        """

        async with self.postgres.session() as session:
            job_id = await session.scalar(
                select(MemoryReembedJobRow.id)
                .where(
                    MemoryReembedJobRow.workspace_id == workspace_id,
                    MemoryReembedJobRow.status.in_(ACTIVE_JOB_STATUSES),
                )
                .limit(1)
            )
        return None if job_id is None else str(job_id)

    async def create_job(
        self,
        *,
        workspace_id: UUID,
        tenant_id: UUID,
        old_embedding_config_id: str | None,
        new_embedding_config_id: str,
        old_model_name: str | None,
        new_model_name: str | None,
        created_by_user_id: UUID | None,
    ) -> JobCreateOutcome:
        """Insert one ``pending`` job for a model change, or say why not.

        The workspace service performs the model switch itself; by the time it
        calls us ``workspaces.embedding`` must already point at
        ``new_embedding_config_id``. A job targeting anything else would be
        self-aborted by the driver's model check, so it is refused instead of
        left to linger as a zombie job.
        """

        async with self.postgres.session() as session:
            workspace = (
                await session.execute(
                    select(
                        Workspace.id,
                        Workspace.tenant_id,
                        Workspace.embedding,
                    )
                    .where(Workspace.id == workspace_id)
                    .with_for_update()
                )
            ).mappings().first()
            if workspace is None or workspace["tenant_id"] != tenant_id:
                return JobCreateOutcome(WORKSPACE_NOT_FOUND)

            active = (
                await session.execute(
                    select(MemoryReembedJobRow.id)
                    .where(
                        MemoryReembedJobRow.workspace_id == workspace_id,
                        MemoryReembedJobRow.status.in_(ACTIVE_JOB_STATUSES),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if active is not None:
                return JobCreateOutcome(ACTIVE_JOB_EXISTS)

            current_embedding = (
                None if workspace["embedding"] is None else str(workspace["embedding"])
            )
            if current_embedding != str(new_embedding_config_id):
                return JobCreateOutcome(MODEL_NOT_CURRENT)

            job = MemoryReembedJobRow(
                id=uuid4(),
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                old_embedding_config_id=old_embedding_config_id,
                new_embedding_config_id=str(new_embedding_config_id),
                status=JOB_STATUS_PENDING,
                old_model_name=old_model_name,
                new_model_name=new_model_name,
                created_by_user_id=created_by_user_id,
                created_at=utcnow_naive(),
            )
            session.add(job)
            await session.commit()
            return JobCreateOutcome(CREATED, job_id=str(job.id))

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
    "ACTIVE_JOB_EXISTS",
    "ACTIVE_JOB_STATUSES",
    "CREATED",
    "JobCreateOutcome",
    "MODEL_CONFLICT",
    "MODEL_NOT_CURRENT",
    "NOT_FOUND",
    "NOTHING_RETRIED",
    "REEMBED_RETRY_BATCH_LIMIT",
    "RETRIED",
    "RetryWriteOutcome",
    "USER_NOT_FAILED",
    "WORKSPACE_NOT_FOUND",
    "ReembedRetryMutationRepository",
]