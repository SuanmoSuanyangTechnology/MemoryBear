"""Publish the dashboard page's background work after the response."""

import logging
from dataclasses import dataclass

from task_bridge.mem_router import QueueName, TaskName, TaskPriority

from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DashboardTask:
    name: str
    queue: str
    payload_fields: tuple[str, ...]
    priority: int | None = None


# These workers remain in the API for now. Keep their established task names,
# payload signatures and unprioritized queue, rather than creating jobs aliases.
_IMPLICIT_EMOTIONS = DashboardTask(
    "app.tasks.init_implicit_emotions_for_users", "memory_heavy_tasks", ("end_user_ids",)
)
_INTEREST = DashboardTask(
    TaskName.INTEREST_DISTRIBUTION_INIT_FOR_USERS.value,
    QueueName.MEMORY_JOBS.value,
    ("workspace_id", "end_user_ids"),
    TaskPriority.NORMAL,
)
_COMMUNITY = DashboardTask(
    "app.tasks.init_community_clustering_for_users",
    "memory_heavy_tasks",
    ("end_user_ids", "workspace_id"),
)
TASK_GROUPS = (
    ("init_tasks", (_IMPLICIT_EMOTIONS, _INTEREST)),
    ("cluster_task", (_COMMUNITY,)),
)


async def publish_dashboard_tasks(
    runtime: ProfileRuntime, context: MemoryPrincipal, end_user_ids: list[str]
) -> None:
    workspace_id = str(context.workspace_id)
    log_args = (context.trace_id, workspace_id, len(end_user_ids))
    page = {"workspace_id": workspace_id, "end_user_ids": end_user_ids}
    for group, tasks in TASK_GROUPS:
        try:
            redis = await runtime.dashboard_redis.client()
            if not await redis.set(
                f"dashboard:{group}:throttle:{workspace_id}", "1", nx=True, ex=60
            ):
                continue
        except Exception:
            logger.exception(
                "Dashboard throttle failed trace=%s workspace=%s hosts=%s group=%s",
                *log_args,
                group,
            )
            continue

        for task in tasks:
            payload = {field: page[field] for field in task.payload_fields}
            try:
                if task.queue == QueueName.MEMORY_JOBS.value:
                    task_id = await runtime.task_bridge.submit(
                        task.name, kwargs=payload, queue=task.queue, priority=task.priority
                    )
                else:
                    task_id = await runtime.api_tasks.submit(
                        task.name, kwargs=payload, queue=task.queue
                    )
            except Exception:
                logger.exception(
                    "Dashboard publish failed trace=%s workspace=%s hosts=%s task=%s",
                    *log_args,
                    task.name,
                )
            else:
                logger.info(
                    "Dashboard published trace=%s workspace=%s hosts=%s task=%s task_id=%s",
                    *log_args,
                    task.name,
                    task_id,
                )
