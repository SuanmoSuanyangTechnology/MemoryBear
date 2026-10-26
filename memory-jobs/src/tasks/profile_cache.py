"""Celery tasks that produce the insight and summary cache columns.

``memory_jobs.profile_cache.generate`` is the per-user unit and
``generate_workspace`` runs the API's serial batch loop (one unit per active
user) and returns its summary. Both run the generation pipelines here; the
profile service only publishes and waits.

Both are safe to retry: the pipelines re-read the source and write only through
the optimistic conditions, and the per-user in-flight lock is keyed by the task
id, so a redelivered message (same id) takes over its own lock while a genuine
concurrent trigger is skipped instead of duplicating LLM work.

Lock ownership stays here: producers (the profile service) submit and poll but
must not take ``insight_summary_cache:inflight:*`` themselves, or the unit would
see a lock it does not own and skip.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task

from src.common.normalization import parse_uuid
from src.modules.profile_cache.service import ProfileCacheGenerationService
from src.runtime import get_runtime
from src.tasks.base import JobTask

logger = logging.getLogger(__name__)

#: Shared with the API's periodic scanner: the same user must not be generated
#: twice at once, whichever service triggered it.
INFLIGHT_KEY_FMT = "insight_summary_cache:inflight:{end_user_id}"
INFLIGHT_TTL_SECONDS = 1800

#: Per-user generation is bounded by the slowest LLM call; the api used the same
#: 15-minute hard limit for this unit.
GENERATE_TIME_LIMIT = 900
GENERATE_SOFT_TIME_LIMIT = 840

#: A workspace batch is bounded by the API's request budget (3600s), not by the
#: per-user limit; it stays below the worker's global hard limit.
GENERATE_WORKSPACE_TIME_LIMIT = 3300
GENERATE_WORKSPACE_SOFT_TIME_LIMIT = 3000


def _inflight_key(end_user_id: str) -> str:
    return INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)


def _acquire_inflight(end_user_id: str, task_id: str | None) -> bool:
    """Take the per-user lock, or confirm that we already hold it.

    A redelivered message keeps its original task id, so the value comparison
    lets that attempt continue instead of skipping forever until the TTL expires.
    A Redis outage degrades open: losing the lock costs a possible duplicate
    generation, while refusing to work costs the user's refresh.
    """

    value = task_id or "1"
    try:
        client = get_runtime().redis.client()
        if client.set(_inflight_key(end_user_id), value, nx=True, ex=INFLIGHT_TTL_SECONDS):
            return True
        return client.get(_inflight_key(end_user_id)) == value
    except Exception:  # pragma: no cover - degrade open rather than block work
        logger.warning("inflight lock unavailable user=%s", end_user_id, exc_info=True)
        return True


def _release_inflight(end_user_id: str, task_id: str | None) -> None:
    """Release the lock only when this attempt still owns it."""

    value = task_id or "1"
    try:
        client = get_runtime().redis.client()
        if client.get(_inflight_key(end_user_id)) == value:
            client.delete(_inflight_key(end_user_id))
    except Exception:  # pragma: no cover - TTL is the backstop
        logger.warning("inflight lock release failed user=%s", end_user_id, exc_info=True)


def _service() -> ProfileCacheGenerationService:
    runtime = get_runtime()
    return ProfileCacheGenerationService(
        runtime.postgres, runtime.neo4j, runtime.model_service
    )


@shared_task(
    name="memory_jobs.profile_cache.generate",
    base=JobTask,
    bind=True,
    ignore_result=False,
    max_retries=0,
    time_limit=GENERATE_TIME_LIMIT,
    soft_time_limit=GENERATE_SOFT_TIME_LIMIT,
)
def generate(
    self: JobTask,
    end_user_id: str,
    workspace_id: str,
    language: str = "zh",
    refresh_insight: bool = True,
    refresh_summary: bool = True,
) -> dict[str, Any]:
    """Generate and persist one user's insight and/or summary cache.

    ``refresh_insight`` / ``refresh_summary`` default to ``True`` for a manual
    trigger; the periodic scanner passes the ones its freshness rules asked for.
    """

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")

    if not _acquire_inflight(end_user_id, self.request.id):
        logger.info(
            "profile cache generation skipped, another refresh is in flight user=%s "
            "workspace=%s",
            end_user_id,
            workspace_id,
        )
        return {
            "status": "skipped",
            "reason": "inflight",
            "end_user_id": end_user_id,
            "workspace_id": workspace_id,
            "task_id": self.request.id,
        }

    try:
        result = _service().generate_cache(
            ws_uuid, end_user_id, language, refresh_insight, refresh_summary
        )
    finally:
        _release_inflight(end_user_id, self.request.id)

    # The payload is exactly the API's per-user result: an all-failed generation
    # is a business outcome carried in ``errors``, not a Celery FAILURE. Only an
    # infrastructure crash makes the task fail.
    if not (result.get("insight_success") or result.get("summary_success")):
        logger.warning(
            "profile cache generation produced no result user=%s workspace=%s errors=%s "
            "elapsed=%.3fs",
            end_user_id,
            workspace_id,
            result.get("errors"),
            time.monotonic() - started_at,
        )
    return result


@shared_task(
    name="memory_jobs.profile_cache.generate_workspace",
    base=JobTask,
    bind=True,
    ignore_result=False,
    max_retries=0,
    # A whole workspace runs serially, one unit per active user; the limit is
    # the API's batch budget, not the per-user one.
    time_limit=GENERATE_WORKSPACE_TIME_LIMIT,
    soft_time_limit=GENERATE_WORKSPACE_SOFT_TIME_LIMIT,
)
def generate_workspace(
    self: JobTask,
    workspace_id: str,
    language: str = "zh",
) -> dict[str, Any]:
    """Generate every active user of one workspace and return the batch summary.

    The loop is the API's: serial, per-user failures collected, one user's
    failure never aborts the batch. It runs here rather than as a fan-out so the
    caller can keep the synchronous contract with a single task id to wait on.
    """

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")

    # Exactly the API's batch result shape: total_users / successful / failed /
    # errors, nothing internal added.
    result = _service().generate_cache(ws_uuid, None, language)
    logger.info(
        "profile cache workspace generation completed workspace=%s total=%s successful=%s "
        "failed=%s elapsed=%.3fs",
        workspace_id,
        result.get("total_users"),
        result.get("successful"),
        result.get("failed"),
        time.monotonic() - started_at,
    )
    return result
