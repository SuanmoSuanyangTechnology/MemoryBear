"""Read-only PostgreSQL reads for the re-embed status endpoints.

These queries answer the same questions as the API's
``memory_reembed_service.list_job_end_users`` / ``get_current_reembed_job``:
which job is "current" for a workspace, and how far each end user inside one job
has progressed. The four display buckets (``failed`` → ``running`` → ``queued`` →
``succeeded``) are the API's vocabulary, not the stored status strings; the SQL
predicates here mirror the API's ``_display_status_condition`` one for one, so a
status filter, the sort order and the ``summary`` counters can never disagree.

Read-only: this repository only selects, so it belongs with the other read-only
PostgreSQL repositories in ``scripts/check_import_boundaries.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import case, func, select
from sqlalchemy.sql.elements import ColumnElement

from src.infrastructure.postgres import PostgresManager
from src.models.references import (
    EndUser,
    MemoryReembedJobRow,
    MemoryReembedJobUserRow,
)

#: Stored per-user statuses, as named by the API's ``ReembedUserStatus``.
USER_STATUS_PENDING = "pending"
USER_STATUS_RUNNING = "running"
USER_STATUS_DONE = "done"
USER_STATUS_FAILED = "failed"

#: Display bucket names; order is the list's sort order and the summary's key order.
DISPLAY_QUEUED = "queued"
DISPLAY_RUNNING = "running"
DISPLAY_SUCCEEDED = "succeeded"
DISPLAY_FAILED = "failed"
DISPLAY_STATUSES = (
    DISPLAY_FAILED,
    DISPLAY_RUNNING,
    DISPLAY_QUEUED,
    DISPLAY_SUCCEEDED,
)

_JOB_COLUMNS = (
    MemoryReembedJobRow.id,
    MemoryReembedJobRow.workspace_id,
    MemoryReembedJobRow.status,
    MemoryReembedJobRow.old_model_name,
    MemoryReembedJobRow.new_model_name,
    MemoryReembedJobRow.total_end_users,
    MemoryReembedJobRow.processed_end_users,
    MemoryReembedJobRow.failed_nodes,
    MemoryReembedJobRow.error,
    MemoryReembedJobRow.created_at,
    MemoryReembedJobRow.started_at,
    MemoryReembedJobRow.finished_at,
)

_USER_COLUMNS = (
    MemoryReembedJobUserRow.end_user_id,
    MemoryReembedJobUserRow.status,
    MemoryReembedJobUserRow.attempts,
    MemoryReembedJobUserRow.total_nodes,
    MemoryReembedJobUserRow.processed_nodes,
    MemoryReembedJobUserRow.failed_nodes,
    MemoryReembedJobUserRow.last_error,
    MemoryReembedJobUserRow.updated_at,
)


def _terminal_failed(
    users: type[MemoryReembedJobUserRow],
    *,
    max_attempts: int,
    stale_before: datetime,
) -> ColumnElement[bool]:
    """``display_status``'s ``failed`` bucket, in SQL.

    Two shapes match: ``failed`` with the retry budget spent, and a stale
    ``running`` row with the budget spent (its worker died on the final attempt).
    """

    return sa.or_(
        sa.and_(
            users.status == USER_STATUS_FAILED,
            users.attempts >= max_attempts,
        ),
        sa.and_(
            users.status == USER_STATUS_RUNNING,
            users.updated_at < stale_before,
            users.attempts >= max_attempts,
        ),
    )


def display_status_condition(
    bucket: str,
    users: type[MemoryReembedJobUserRow],
    *,
    max_attempts: int,
    stale_before: datetime,
) -> ColumnElement[bool]:
    """SQL dual of the service layer's ``display_status``; raises when unknown.

    An unknown bucket must fail loudly: silently falling back to one branch would
    make a misspelled ``?status=`` return unrelated rows.
    """

    done = users.status == USER_STATUS_DONE
    pending = users.status == USER_STATUS_PENDING
    terminal_failed = _terminal_failed(
        users, max_attempts=max_attempts, stale_before=stale_before
    )
    if bucket == DISPLAY_SUCCEEDED:
        return done
    if bucket == DISPLAY_FAILED:
        return terminal_failed
    if bucket == DISPLAY_QUEUED:
        return pending
    if bucket == DISPLAY_RUNNING:
        # Everything that ran and is not terminal: running, failed-with-budget
        # left (an automatic re-dispatch will pick it up), or stale crash.
        return sa.and_(~done, ~terminal_failed, ~pending)
    raise ValueError(f"unknown reembed display status: {bucket}")


def _display_status_rank(
    users: type[MemoryReembedJobUserRow],
    *,
    max_attempts: int,
    stale_before: datetime,
) -> ColumnElement[int]:
    """Sort rank: failed → running → queued → succeeded (the API's contract)."""

    return case(
        *[
            (
                display_status_condition(
                    bucket,
                    users,
                    max_attempts=max_attempts,
                    stale_before=stale_before,
                ),
                rank,
            )
            for rank, bucket in enumerate(DISPLAY_STATUSES, start=1)
        ],
        else_=len(DISPLAY_STATUSES) + 1,
    )


class ReembedStatusRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def current_job(self, workspace_id: UUID) -> dict[str, Any] | None:
        """The newest job of the workspace, expired or not (the caller decides)."""

        statement = (
            select(*_JOB_COLUMNS)
            .where(MemoryReembedJobRow.workspace_id == workspace_id)
            .order_by(MemoryReembedJobRow.created_at.desc())
            .limit(1)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return dict(row) if row is not None else None

    async def job(self, job_id: UUID) -> dict[str, Any] | None:
        """One job by id, regardless of status (terminal jobs stay addressable)."""

        statement = select(*_JOB_COLUMNS).where(MemoryReembedJobRow.id == job_id).limit(1)
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return dict(row) if row is not None else None

    async def end_user_page(
        self,
        *,
        job_id: UUID,
        status: str | None,
        stale_before: datetime,
        max_attempts: int,
        page: int,
        pagesize: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of a job's per-user rows plus the filtered total."""

        users = MemoryReembedJobUserRow
        conditions: list[ColumnElement[bool]] = [users.job_id == job_id]
        if status is not None:
            conditions.append(
                display_status_condition(
                    status,
                    users,
                    max_attempts=max_attempts,
                    stale_before=stale_before,
                )
            )
        offset = max(page - 1, 0) * pagesize
        statement = (
            select(*_USER_COLUMNS)
            .where(*conditions)
            .order_by(
                _display_status_rank(
                    users, max_attempts=max_attempts, stale_before=stale_before
                ).asc(),
                users.end_user_id.asc(),
            )
            .offset(offset)
            .limit(pagesize)
        )
        async with self.postgres.session() as session:
            total = await session.scalar(
                select(func.count()).select_from(select(users.id).where(*conditions).subquery())
            )
            rows = (await session.execute(statement)).mappings().all()
        return [dict(row) for row in rows], int(total or 0)

    async def summary(
        self,
        *,
        job_id: UUID,
        stale_before: datetime,
        max_attempts: int,
    ) -> dict[str, int]:
        """Four-bucket counts over the whole job (unaffected by a status filter)."""

        users = MemoryReembedJobUserRow
        counts = [
            func.count(users.id)
            .filter(
                display_status_condition(
                    bucket,
                    users,
                    max_attempts=max_attempts,
                    stale_before=stale_before,
                )
            )
            .label(bucket)
            for bucket in DISPLAY_STATUSES
        ]
        statement = select(*counts).where(users.job_id == job_id)
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).one()
        return {
            bucket: int(value or 0)
            for bucket, value in zip(DISPLAY_STATUSES, row, strict=True)
        }

    async def end_user_labels(self, end_user_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Display name / external id for the rows on one page.

        ``memory_reembed_job_users.end_user_id`` stores ``end_users.id`` as text,
        so ids that no longer parse as UUIDs are skipped instead of failing the
        page.
        """

        ids: list[UUID] = []
        for raw in end_user_ids:
            try:
                ids.append(UUID(str(raw)))
            except (TypeError, ValueError):
                continue
        if not ids:
            return {}
        statement = select(EndUser.id, EndUser.other_name, EndUser.other_id).where(
            EndUser.id.in_(ids)
        )
        async with self.postgres.session() as session:
            rows = (await session.execute(statement)).all()
        return {
            str(row[0]): {"name": row[1] or None, "external_id": row[2] or None}
            for row in rows
        }