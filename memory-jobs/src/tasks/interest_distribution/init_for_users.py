"""Pre-warm interest distributions for the users a dashboard just returned.

Ported from the API's ``app.tasks.init_interest_distribution_for_users``: the
trigger (``/dashboard/end_users``) stays in the API, the work runs here.

The task is dispatch-only — one ``INTEREST_DISTRIBUTION_GENERATE`` unit per user
without a cached payload — so a large workspace answers immediately instead of
pinning one worker slot for the whole fan-out. Each unit is told to write the
cache, which is the point of a pre-warm: the next online request hits it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName, TaskPriority

from src.common.normalization import normalize_language, parse_uuid
from src.modules.interest_distribution.cache import is_cached
from src.modules.shared.repositories.end_user import EndUserRepository
from src.runtime import get_runtime
from src.tasks.base import JobTask
from src.tasks.interest_distribution.dispatch import dispatch_generate

logger = logging.getLogger(__name__)

#: Enumerate + dispatch only; the queued units carry the LLM work.
INIT_TIME_LIMIT = 600
INIT_SOFT_TIME_LIMIT = 540

#: The API's pre-warm hardcoded zh at its display limit; keep both until the
#: dashboard gains a language preference.
INIT_LANGUAGE = "zh"
INIT_LIMIT = 5


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
    """Queue one generation unit per listed user that has no cached payload.

    Users outside the workspace's active set are skipped rather than failed: the
    dashboard list may be a moment stale, and a merged or removed user has
    nothing to pre-warm.
    """

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")
    language = normalize_language(language)

    candidates: list[str] = []
    invalid = 0
    for raw in end_user_ids:
        parsed = parse_uuid(raw)
        if parsed is None:
            invalid += 1
            logger.warning("interest pre-warm skipped an invalid user id user=%r", raw)
            continue
        candidates.append(str(parsed))

    active = {
        str(user_id)
        for user_id in EndUserRepository(
            get_runtime().postgres
        ).active_ids_in_workspace(ws_uuid)
    }

    dispatched = 0
    skipped_cached = 0
    skipped_missing = 0
    errors: list[dict[str, Any]] = []
    # ``dict.fromkeys`` keeps the caller's order while collapsing repeats.
    for end_user_id in dict.fromkeys(candidates):
        if end_user_id not in active:
            skipped_missing += 1
            continue
        if is_cached(end_user_id, language):
            skipped_cached += 1
            continue
        try:
            dispatch_generate(
                end_user_id=end_user_id,
                workspace_id=str(ws_uuid),
                language=language,
                limit=limit,
                write_cache=True,
                # Pre-warm serves the next request, not a caller waiting now: it
                # sits with the bulk fan-out, below anything interactive.
                priority=TaskPriority.NORMAL,
                index=dispatched,
            )
        except Exception as exc:
            errors.append({"end_user_id": end_user_id, "error": str(exc)})
            logger.error(
                "interest pre-warm dispatch failed user=%s workspace=%s",
                end_user_id,
                workspace_id,
                exc_info=True,
            )
            continue
        dispatched += 1

    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "interest pre-warm dispatched=%s skipped_cached=%s skipped_missing=%s "
        "invalid=%s failed=%s elapsed=%.3fs",
        dispatched,
        skipped_cached,
        skipped_missing,
        invalid,
        len(errors),
        elapsed_seconds,
    )
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "skipped_cached": skipped_cached,
        "skipped_missing": skipped_missing,
        "invalid": invalid,
        "failed": len(errors),
        "errors": errors,
        "elapsed_seconds": elapsed_seconds,
        "task_id": self.request.id,
    }
