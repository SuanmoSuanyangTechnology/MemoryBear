"""Use case for the re-embed status list endpoints.

The API exposes per-user progress of a workspace's stock-vector rebuild in two
shapes: "the current job" (the newest job, until a terminal job is 24h old) and
"this job id" (any job, terminal ones included). Both share one page builder, so
the filters, sort order and ``summary`` counters cannot drift between them.

The pure policy functions below are the API's, ported verbatim:
``is_user_terminal`` / ``display_status`` decide the four display buckets, and
``current_job_expired`` owns the 24h visibility window. Only the bucket names
cross the wire — the stored status strings and ``attempts`` stay internal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.common.pagination import PageMeta
from src.common.timing import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.repositories.reembed_status import (
    DISPLAY_FAILED,
    DISPLAY_QUEUED,
    DISPLAY_RUNNING,
    DISPLAY_STATUSES,
    DISPLAY_SUCCEEDED,
    ReembedStatusRepository,
)
from src.schemas.workspace_api import (
    MemoryReembedEndUserListData,
    MemoryReembedJobData,
    ReembedEndUserItem,
)

#: Attempts one end user gets before a failure becomes terminal (API contract).
REEMBED_MAX_ATTEMPTS = 3
#: A ``running`` row older than this means its worker died; it may be re-claimed.
REEMBED_STALE_RUNNING_SECONDS = 15 * 60
#: A terminal job stops being "the current job" this long after it ended.
REEMBED_CURRENT_JOB_VISIBLE_SECONDS = 24 * 60 * 60

#: Job statuses that are still in flight and never age out of "current".
_ACTIVE_JOB_STATUSES = ("pending", "running")


class InvalidReembedStatusError(ValueError):
    """The ``status`` filter is not one of the four display buckets."""


class ReembedJobNotFoundError(Exception):
    """The requested job does not exist, or belongs to another workspace."""


def is_user_terminal(
    status: str, attempts: int, *, stale_running: bool = False
) -> bool:
    """Whether no further work will be attempted for one end_user row.

    Single policy for the two decisions that must agree: a job may only finalize
    once every row is terminal, and the driver may only claim non-terminal rows.
    """

    if status == "done":
        return True
    if status == "failed":
        return attempts >= REEMBED_MAX_ATTEMPTS
    if status == "running":
        return stale_running and attempts >= REEMBED_MAX_ATTEMPTS
    return False


def display_status(status: str, attempts: int, *, stale_running: bool) -> str:
    """The row's display bucket, derived from the same predicate as terminality.

    ``queued`` is reserved for rows never attempted; anything that ran and is not
    terminal (running, failed-with-budget-left, stale crash) reads as ``running``.
    """

    if status == "done":
        return DISPLAY_SUCCEEDED
    if is_user_terminal(status, attempts, stale_running=stale_running):
        return DISPLAY_FAILED
    if status == "pending":
        return DISPLAY_QUEUED
    return DISPLAY_RUNNING


def current_job_expired(
    job: dict[str, Any], *, now: datetime | None = None
) -> bool:
    """Whether the newest job is too old to still be shown as "the current job".

    In-flight jobs never expire. A terminal job ages off based on when it ended
    (falling back to ``created_at``), not when it started: a job that ran for a
    long time and finished a minute ago is exactly what the user just saw.
    """

    if job["status"] in _ACTIVE_JOB_STATUSES:
        return False
    ended_at = job["finished_at"] or job["created_at"]
    if ended_at is None:
        return False
    reference = now or utcnow_naive()
    return ended_at < reference - timedelta(seconds=REEMBED_CURRENT_JOB_VISIBLE_SECONDS)


def to_timestamp_ms(value: datetime | None) -> int | None:
    """Serialize a datetime as epoch milliseconds, naive values read as UTC."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return int(value.timestamp() * 1000)


def empty_job_end_user_page(*, page: int, pagesize: int) -> dict[str, Any]:
    """The "no current job" payload: an empty page, not an empty object.

    Clients read ``data.items.length`` and the four summary keys, so the empty
    state keeps the paginated shape (``job_id`` is null to distinguish it from a
    job that happens to have no rows).
    """

    return MemoryReembedEndUserListData(
        job_id=None,
        job_status="",
        page=PageMeta.create(page=page, pagesize=pagesize, total=0),
        summary={bucket: 0 for bucket in DISPLAY_STATUSES},
        items=[],
    ).model_dump(mode="json")


class ReembedStatusService:
    def __init__(self, postgres: PostgresManager) -> None:
        self.repository = ReembedStatusRepository(postgres)

    async def current_job(self, workspace_id: UUID) -> dict[str, Any] | None:
        """The workspace's visible current job, or ``None`` when there is none.

        One source for both the list endpoint and the one-click retries: the 24h
        visibility window must not disagree between "what the list shows" and
        "what the retry button acts on".
        """

        job = await self.repository.current_job(workspace_id)
        if job is None or current_job_expired(job):
            return None
        return job

    async def current_job_detail(self, workspace_id: UUID) -> dict[str, Any] | None:
        """The visible current job with its four-bucket breakdown, or ``None``."""

        job = await self.current_job(workspace_id)
        if job is None:
            return None
        return await self._job_payload(job)

    async def job_detail(
        self, workspace_id: UUID, job_id: UUID
    ) -> dict[str, Any]:
        """One job by id (terminal ones included), scoped to the workspace."""

        job = await self.repository.job(job_id)
        if job is None or job["workspace_id"] != workspace_id:
            raise ReembedJobNotFoundError(f"reembed job not found: {job_id}")
        return await self._job_payload(job)

    async def _job_payload(self, job: dict[str, Any]) -> dict[str, Any]:
        """Serialize a job row plus its durable per-user breakdown.

        Same field set and vocabulary as the API's ``build_reembed_job_payload``:
        the four buckets come from the list endpoint's own summary query, so the
        detail page's tabs and the list's ``summary`` can never disagree.
        """

        summary = await self.repository.summary(
            job_id=job["id"],
            stale_before=utcnow_naive()
            - timedelta(seconds=REEMBED_STALE_RUNNING_SECONDS),
            max_attempts=REEMBED_MAX_ATTEMPTS,
        )
        return MemoryReembedJobData(
            id=str(job["id"]),
            workspace_id=str(job["workspace_id"]),
            status=str(job["status"] or ""),
            old_model_name=job["old_model_name"],
            new_model_name=job["new_model_name"],
            total_end_users=int(job["total_end_users"] or 0),
            processed_end_users=int(job["processed_end_users"] or 0),
            failed_nodes=int(job["failed_nodes"] or 0),
            end_users=summary,
            error=job["error"],
            created_at=to_timestamp_ms(job["created_at"]),
            started_at=to_timestamp_ms(job["started_at"]),
            finished_at=to_timestamp_ms(job["finished_at"]),
        ).model_dump(mode="json")

    async def current_end_users(
        self,
        workspace_id: UUID,
        *,
        status: str | None,
        page: int,
        pagesize: int,
    ) -> dict[str, Any]:
        _validate_status(status)
        job = await self.current_job(workspace_id)
        if job is None:
            return empty_job_end_user_page(page=page, pagesize=pagesize)
        return await self._job_page(job, status=status, page=page, pagesize=pagesize)

    async def job_end_users(
        self,
        workspace_id: UUID,
        job_id: UUID,
        *,
        status: str | None,
        page: int,
        pagesize: int,
    ) -> dict[str, Any]:
        _validate_status(status)
        job = await self.repository.job(job_id)
        if job is None or job["workspace_id"] != workspace_id:
            raise ReembedJobNotFoundError(f"reembed job not found: {job_id}")
        return await self._job_page(job, status=status, page=page, pagesize=pagesize)

    async def _job_page(
        self,
        job: dict[str, Any],
        *,
        status: str | None,
        page: int,
        pagesize: int,
    ) -> dict[str, Any]:
        stale_before = utcnow_naive() - timedelta(
            seconds=REEMBED_STALE_RUNNING_SECONDS
        )
        rows, total = await self.repository.end_user_page(
            job_id=job["id"],
            status=status,
            stale_before=stale_before,
            max_attempts=REEMBED_MAX_ATTEMPTS,
            page=page,
            pagesize=pagesize,
        )
        labels = await self.repository.end_user_labels(
            [str(row["end_user_id"]) for row in rows]
        )
        summary = await self.repository.summary(
            job_id=job["id"],
            stale_before=stale_before,
            max_attempts=REEMBED_MAX_ATTEMPTS,
        )
        items = [
            _item(row, label=labels.get(str(row["end_user_id"]), {}), stale_before=stale_before)
            for row in rows
        ]
        return MemoryReembedEndUserListData(
            job_id=str(job["id"]),
            job_status=str(job["status"] or ""),
            page=PageMeta.create(page=page, pagesize=pagesize, total=total),
            summary=summary,
            items=items,
        ).model_dump(mode="json")


def _validate_status(status: str | None) -> None:
    if status is not None and status not in DISPLAY_STATUSES:
        raise InvalidReembedStatusError(f"未知的状态过滤值: {status}")


def _item(
    row: dict[str, Any],
    *,
    label: dict[str, Any],
    stale_before: datetime,
) -> ReembedEndUserItem:
    attempts = int(row["attempts"] or 0)
    updated_at = row["updated_at"]
    return ReembedEndUserItem(
        end_user_id=str(row["end_user_id"]),
        name=label.get("name"),
        external_id=label.get("external_id"),
        status=display_status(
            str(row["status"]),
            attempts,
            stale_running=bool(updated_at is not None and updated_at < stale_before),
        ),
        attempts=attempts,
        max_attempts=REEMBED_MAX_ATTEMPTS,
        total_nodes=int(row["total_nodes"] or 0),
        processed_nodes=int(row["processed_nodes"] or 0),
        failed_nodes=int(row["failed_nodes"] or 0),
        last_error=row["last_error"],
        updated_at=to_timestamp_ms(updated_at),
    )