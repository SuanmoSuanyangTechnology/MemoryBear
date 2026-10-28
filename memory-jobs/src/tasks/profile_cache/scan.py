"""Periodic sweep that dispatches stale insight/summary caches.

Ported from the API's ``scan_refresh_insight_summary_cache``: this task only
enumerates and dispatches — no LLM, no Neo4j, no full metadata reads — so one
sweep never holds a connection for long. Every hit becomes one
``memory_jobs.profile_cache.generate`` unit, carrying the per-column verdict so
the worker regenerates only what is actually stale. Its beat entry is declared
in ``src/tasks/schedules.py``, the single place periodic tasks are managed.

Coexistence with the API's scanner: both use the same
``insight_summary_cache:inflight:*`` key namespace. This scanner only *reads*
it before dispatching (the per-user task owns the lock; taking it here would
make the unit skip itself), so a user already being refreshed by the API's
chain is skipped instead of queued twice.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName, TaskPriority

from src.modules.profile_cache.refresh import classify_cache_refresh, utcnow_naive
from src.modules.shared.repositories.end_user import EndUserRepository
from src.runtime import get_runtime
from src.tasks.base import JobTask
from src.tasks.profile_cache.dispatch import dispatch_generate
from src.tasks.profile_cache.inflight import is_inflight

logger = logging.getLogger(__name__)

#: Enumeration plus dispatch only; the API used the same 10-minute budget.
SCAN_TIME_LIMIT = 600
SCAN_SOFT_TIME_LIMIT = 540

#: The API's scan hardcodes Chinese for the refresh; keep the same behavior
#: until a per-user language preference exists.
SCAN_LANGUAGE = "zh"


@shared_task(
    name=TaskName.PROFILE_CACHE_SCAN.value,
    base=JobTask,
    bind=True,
    ignore_result=False,
    max_retries=0,
    # A lost scan only means the next nightly run sweeps again; late acks would
    # just re-enumerate a sweep that is safe to repeat.
    acks_late=False,
    time_limit=SCAN_TIME_LIMIT,
    soft_time_limit=SCAN_SOFT_TIME_LIMIT,
)
def scan(self: JobTask) -> dict[str, Any]:
    """Dispatch one ``generate`` unit per user whose cache is out of date."""

    started_at = time.monotonic()
    runtime = get_runtime()
    repository = EndUserRepository(runtime.postgres)
    fresh_hours = runtime.settings.memory_cache_fresh_hours
    # One "now" for the whole sweep: every user is judged against the same
    # instant, so a long scan does not gradually widen the freshness window.
    now = utcnow_naive()

    dispatched = 0
    skip_no_change = 0
    skip_fresh = 0
    skip_inflight = 0
    failed = 0

    for workspace_id in repository.active_workspace_ids():
        # One query per workspace keeps memory flat on large tenants.
        for row in repository.cache_refresh_rows(workspace_id):
            end_user_id = str(row.end_user_id)
            try:
                decisions = classify_cache_refresh(
                    insight_at=row.memory_insight_updated_at,
                    summary_at=row.user_summary_updated_at,
                    write_at=row.write_time,
                    metadata_updated_at=row.metadata_updated_at,
                    now=now,
                    fresh_hours=fresh_hours,
                )
                refresh_insight = decisions.insight == "dispatch"
                refresh_summary = decisions.summary == "dispatch"
                if not refresh_insight and not refresh_summary:
                    if "skip_fresh" in (decisions.insight, decisions.summary):
                        skip_fresh += 1
                    else:
                        skip_no_change += 1
                    continue
                if is_inflight(end_user_id):
                    skip_inflight += 1
                    continue

                dispatch_generate(
                    end_user_id=end_user_id,
                    workspace_id=str(row.workspace_id),
                    language=SCAN_LANGUAGE,
                    refresh_insight=refresh_insight,
                    refresh_summary=refresh_summary,
                    # The sweep yields to every user-triggered unit; the beat
                    # entry that starts the sweep carries the same level.
                    priority=TaskPriority.BACKGROUND,
                    index=dispatched,
                )
            except Exception:
                # One refused publish must not strand the rest of the sweep: the
                # API's scanner isolated every row the same way. A broker error
                # from ``apply_async`` is the common case, but classification and
                # the inflight probe are per-user too.
                failed += 1
                logger.error(
                    "profile cache scan dispatch failed user=%s workspace=%s",
                    end_user_id,
                    workspace_id,
                    exc_info=True,
                    extra={"event": "profile_cache_scan_user_failed"},
                )
                continue
            dispatched += 1
            if dispatched % 50 == 0:
                logger.info(
                    "profile cache scan progress dispatched=%s",
                    dispatched,
                    extra={"event": "profile_cache_scan_progress"},
                )

    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "profile cache scan completed dispatched=%s skip_no_change=%s skip_fresh=%s "
        "skip_inflight=%s failed=%s elapsed=%.3fs",
        dispatched,
        skip_no_change,
        skip_fresh,
        skip_inflight,
        failed,
        elapsed_seconds,
        extra={"event": "profile_cache_scan_completed"},
    )
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "skip_no_change": skip_no_change,
        "skip_fresh": skip_fresh,
        "skip_inflight": skip_inflight,
        "failed": failed,
        "elapsed_seconds": elapsed_seconds,
        "task_id": self.request.id,
    }
