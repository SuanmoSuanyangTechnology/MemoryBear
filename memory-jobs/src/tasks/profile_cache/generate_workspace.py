"""Celery task that fans a workspace out into per-user cache units.

It answers in the API's batch shape instead of running the batch itself: the
generation runs in the per-user units this task queues, so the caller's wait
stays short and the API's 3600s batch budget no longer applies.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from celery import shared_task
from task_bridge.mem_router import TaskName, TaskPriority

from src.common.normalization import parse_uuid
from src.modules.shared.repositories.end_user import EndUserRepository
from src.modules.shared.repositories.workspace import WorkspaceRepository
from src.modules.shared.workspace_validation import validate_workspace_row
from src.runtime import get_runtime
from src.tasks.base import JobTask
from src.tasks.profile_cache.dispatch import dispatch_generate

logger = logging.getLogger(__name__)

#: The workspace unit only enumerates and dispatches; the generation itself runs
#: in the per-user units, so the API's 3600s batch budget no longer applies.
GENERATE_WORKSPACE_TIME_LIMIT = 600
GENERATE_WORKSPACE_SOFT_TIME_LIMIT = 540


def _workspace_user_ids(workspace_id: UUID) -> list[UUID]:
    """Validate the workspace once, then list the users the batch covers.

    Each unit re-validates its own workspace; this check only keeps an
    unsupported workspace from queueing N units that would all fail.
    """

    postgres = get_runtime().postgres
    workspace_row = WorkspaceRepository(postgres).workspace_row(workspace_id)
    valid, reason, storage_type = validate_workspace_row(workspace_row)
    if not valid:
        raise ValueError(
            f"workspace {workspace_id} cannot generate cache: {reason} "
            f"(storage_type={storage_type or 'unknown'})"
        )
    return EndUserRepository(postgres).active_ids_in_workspace(workspace_id)


@shared_task(
    name=TaskName.PROFILE_CACHE_GENERATE_WORKSPACE.value,
    base=JobTask,
    bind=True,
    ignore_result=False,
    max_retries=0,
    # Enumeration plus dispatch only: the generation runs in the per-user units
    # this task queues, so the API's batch budget no longer applies here.
    time_limit=GENERATE_WORKSPACE_TIME_LIMIT,
    soft_time_limit=GENERATE_WORKSPACE_SOFT_TIME_LIMIT,
)
def generate_workspace(
    self: JobTask,
    workspace_id: str,
    language: str = "zh",
) -> dict[str, Any]:
    """Fan the workspace out into per-user units and answer in the API's shape.

    Enumerating and dispatching here (rather than running the serial batch
    inline) keeps the caller's wait short. The result keeps the API's batch
    shape — ``total_users`` / ``successful`` / ``failed`` / ``errors`` — where
    ``successful`` counts the units that were queued and ``failed`` the ones the
    broker refused; per-user generation outcomes stay in the units themselves.
    """

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")

    user_ids = _workspace_user_ids(ws_uuid)
    successful = 0
    dispatch_errors: list[dict[str, Any]] = []
    for index, user_id in enumerate(user_ids):
        try:
            dispatch_generate(
                end_user_id=str(user_id),
                workspace_id=workspace_id,
                language=language,
                refresh_insight=True,
                refresh_summary=True,
                # Bulk units: the caller waits only for the dispatch summary,
                # so they sit below a single-user refresh but above the nightly
                # sweep. A thousand units at INTERACTIVE would starve the ones
                # users are actually waiting on.
                priority=TaskPriority.NORMAL,
                index=index,
            )
        except Exception as exc:
            dispatch_errors.append({"end_user_id": str(user_id), "error": str(exc)})
            logger.error(
                "profile cache workspace dispatch failed user=%s workspace=%s",
                user_id,
                workspace_id,
                exc_info=True,
            )
            continue
        successful += 1

    logger.info(
        "profile cache workspace dispatched workspace=%s total=%s queued=%s failed=%s "
        "elapsed=%.3fs",
        workspace_id,
        len(user_ids),
        successful,
        len(dispatch_errors),
        time.monotonic() - started_at,
    )
    # Exactly the API's batch result shape: total_users / successful / failed /
    # errors, nothing internal added.
    return {
        "total_users": len(user_ids),
        "successful": successful,
        "failed": len(dispatch_errors),
        "errors": dispatch_errors,
    }
