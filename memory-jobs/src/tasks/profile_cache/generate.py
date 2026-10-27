"""Celery task that produces one user's insight and summary cache columns.

``memory_jobs.profile_cache.generate`` is the per-user unit; ``generate_workspace``
fans a workspace out into these units and ``scan`` dispatches the stale ones.
All of them share ``dispatch`` (staggered enqueue) and ``inflight`` (the
per-user lock) inside this package.

Safe to retry: the pipelines re-read the source and write only through the
optimistic conditions, and the per-user in-flight lock is keyed by the task id,
so a redelivered message (same id) takes over its own lock while a genuine
concurrent trigger is skipped instead of duplicating LLM work.

Lock ownership stays in this unit: producers (the profile service) submit and
poll but must not take ``insight_summary_cache:inflight:*`` themselves, or the
unit would see a lock it does not own and skip. The sweep only *reads* the key
to avoid queueing a user someone else is already refreshing.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName

from src.common.normalization import parse_uuid
from src.modules.profile_cache.service import ProfileCacheGenerationService
from src.runtime import get_runtime
from src.tasks.base import JobTask
from src.tasks.profile_cache.inflight import acquire_inflight, release_inflight

logger = logging.getLogger(__name__)

#: Per-user generation is bounded by the slowest LLM call; the api used the same
#: 15-minute hard limit for this unit.
GENERATE_TIME_LIMIT = 900
GENERATE_SOFT_TIME_LIMIT = 840


def _service() -> ProfileCacheGenerationService:
    runtime = get_runtime()
    return ProfileCacheGenerationService(
        runtime.postgres, runtime.neo4j, runtime.model_service
    )


@shared_task(
    name=TaskName.PROFILE_CACHE_GENERATE.value,
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

    if not acquire_inflight(end_user_id, self.request.id):
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
        release_inflight(end_user_id, self.request.id)

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
