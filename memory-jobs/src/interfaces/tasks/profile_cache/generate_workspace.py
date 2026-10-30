"""Celery task that fans a workspace out into per-user cache units.

It answers in the API's batch shape instead of running the batch itself: the
generation runs in the per-user units this task queues.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName

from src.composition import get_container
from src.domain.shared.ids import WorkspaceId, parse_uuid
from src.interfaces.tasks.base import JobTask

logger = logging.getLogger(__name__)

#: The workspace unit only enumerates and dispatches; the generation itself runs
#: in the per-user units, so the API's 3600s batch budget no longer applies.
GENERATE_WORKSPACE_TIME_LIMIT = 600
GENERATE_WORKSPACE_SOFT_TIME_LIMIT = 540


def _use_case(language: str):
    return get_container().profile_cache.fan_out(language)


@shared_task(
    name=TaskName.PROFILE_CACHE_GENERATE_WORKSPACE.value,
    base=JobTask,
    bind=True,
    ignore_result=False,
    max_retries=0,
    time_limit=GENERATE_WORKSPACE_TIME_LIMIT,
    soft_time_limit=GENERATE_WORKSPACE_SOFT_TIME_LIMIT,
)
def generate_workspace(
    self: JobTask,
    workspace_id: str,
    language: str = "zh",
) -> dict[str, Any]:
    """Fan the workspace out into per-user units and answer in the API's shape."""

    ws_uuid = parse_uuid(workspace_id)
    if ws_uuid is None:
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")

    return _use_case(language).execute(WorkspaceId(ws_uuid)).to_payload()
