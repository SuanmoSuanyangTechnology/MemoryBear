"""Pre-warm interest distributions for the users a dashboard just returned.

This task parses the caller's ids and renders ``PrewarmInterestDistributionsUseCase``;
the filtering and publishing live in the use case.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName

from src.common.normalization import normalize_language, parse_uuid
from src.composition import get_container
from src.domain.shared.ids import EndUserId, WorkspaceId
from src.interfaces.tasks.base import JobTask

logger = logging.getLogger(__name__)

#: Enumerate + dispatch only; the queued units carry the LLM work.
INIT_TIME_LIMIT = 600
INIT_SOFT_TIME_LIMIT = 540

#: The API's pre-warm hardcoded zh at its display limit; keep both until the
#: dashboard gains a language preference.
INIT_LANGUAGE = "zh"
INIT_LIMIT = 5


def _use_case(language: str, limit: int):
    return get_container().prewarm_interest_distributions(language, limit)


@shared_task(
    name=TaskName.INTEREST_DISTRIBUTION_INIT_FOR_USERS.value,
    base=JobTask,
    bind=True,
    # Fire-and-forget: the dashboard dispatch never reads the result.
    ignore_result=True,
    max_retries=0,
    # A lost run only means the next dashboard visit enqueues it again, so late
    # acks would just repeat a sweep that is safe to repeat.
    acks_late=False,
    time_limit=INIT_TIME_LIMIT,
    soft_time_limit=INIT_SOFT_TIME_LIMIT,
)
def init_for_users(
    self: JobTask,
    workspace_id: str,
    end_user_ids: list[str],
    language: str = INIT_LANGUAGE,
    limit: int = INIT_LIMIT,
) -> dict[str, Any]:
    """Queue one generation unit per listed user that has no cached payload."""

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")
    language = normalize_language(language)

    candidates: list[EndUserId] = []
    invalid = 0
    for raw in end_user_ids:
        parsed = parse_uuid(raw)
        if parsed is None:
            invalid += 1
            logger.warning("interest pre-warm skipped an invalid user id user=%r", raw)
            continue
        candidates.append(EndUserId(parsed))

    report = _use_case(language, limit).execute(WorkspaceId(ws_uuid), candidates)
    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "interest pre-warm dispatched=%s skipped_cached=%s skipped_missing=%s "
        "invalid=%s failed=%s elapsed=%.3fs",
        report.dispatched,
        report.skipped_cached,
        report.skipped_missing,
        invalid,
        report.failed,
        elapsed_seconds,
    )
    return {
        "status": "SUCCESS",
        "dispatched": report.dispatched,
        "skipped_cached": report.skipped_cached,
        "skipped_missing": report.skipped_missing,
        "invalid": invalid,
        "failed": report.failed,
        "errors": report.errors,
        "elapsed_seconds": elapsed_seconds,
        "task_id": self.request.id,
    }
