"""Celery task that produces one user's insight and summary cache columns.

The application use case owns the orchestration; this module binds the task
name, takes the use case from the composition root and returns the JSON payload.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName

from src.application.profile_cache.generate import GenerateProfileCacheCommand
from src.composition import get_container
from src.domain.shared.ids import WorkspaceId, parse_uuid
from src.interfaces.tasks.base import JobTask

logger = logging.getLogger(__name__)

#: Per-user generation is bounded by the slowest LLM call; the api used the same
#: 15-minute hard limit for this unit.
GENERATE_TIME_LIMIT = 900
GENERATE_SOFT_TIME_LIMIT = 840


def _use_case():
    return get_container().profile_cache.generate()


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
    """Generate and persist one user's insight and/or summary cache."""

    started_at = time.monotonic()
    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")

    outcome = _use_case().execute(
        GenerateProfileCacheCommand(
            end_user_id=end_user_id,
            workspace_id=WorkspaceId(ws_uuid),
            # The Celery task id is this attempt's token; the application
            # only sees the opaque value.
            attempt_token=str(self.request.id or ""),
            language=language,
            refresh_insight=refresh_insight,
            refresh_summary=refresh_summary,
        )
    )
    result = outcome.to_payload()
    if outcome.skipped_reason is not None:
        result["task_id"] = self.request.id
        return result

    requested = [
        name
        for name, enabled in (
            ("insight", refresh_insight),
            ("summary", refresh_summary),
        )
        if enabled
    ]
    outcomes = [bool(result.get(f"{name}_success")) for name in requested]
    if requested and not any(outcomes):
        # The API's worker contract: a unit that refreshed nothing it was asked
        # to refresh is a task failure, not a success holding empty columns.
        # Partial success stays a successful task with per-part errors.
        errors = {
            str(item.get("type")): item.get("error")
            for item in result.get("errors") or []
        }
        raise RuntimeError(
            "all requested cache refreshes failed: "
            + ", ".join(
                f"{name}_error={errors.get(name)!r}" for name in requested
            )
        )
    if requested and not all(outcomes):
        logger.warning(
            "profile cache generation produced a partial result user=%s workspace=%s "
            "errors=%s elapsed=%.3fs",
            end_user_id,
            workspace_id,
            result.get("errors"),
            time.monotonic() - started_at,
        )
    return result
