"""Direct API-worker task publishing for migrated endpoints.

The API host registers these names in ``api/app/tasks.py``; profile only
publishes them. No gateway task sits in between: when an endpoint's API logic
dispatches Celery work (e.g. the reflection after a merge), the profile
publishes that task itself.
"""

from __future__ import annotations

import uuid

from task_bridge.mem_router import TaskPriority

#: Registered by the API's ``api/app/tasks.py``; consumed on ``reflection_tasks``.
API_REFLECTION_LAYER2_TASK = "app.tasks.do_layer2_reflection"
API_REFLECTION_QUEUE = "reflection_tasks"


async def dispatch_layer2_reflection(
    task_bridge: object,
    *,
    end_user_id: uuid.UUID | str,
    config_id: uuid.UUID | str,
    workspace_id: uuid.UUID | str,
) -> str:
    """Publish ``do_layer2_reflection`` straight to the API worker.

    ``from_retry=True`` skips the activity/period gate: a merge must always be
    followed by a reflection.
    """

    return await task_bridge.submit(  # type: ignore[attr-defined]
        API_REFLECTION_LAYER2_TASK,
        kwargs={
            "end_user_id": str(end_user_id),
            "config_id": str(config_id),
            "workspace_id": str(workspace_id),
            "from_retry": True,
        },
        queue=API_REFLECTION_QUEUE,
        priority=TaskPriority.BACKGROUND,
    )


__all__ = [
    "API_REFLECTION_LAYER2_TASK",
    "API_REFLECTION_QUEUE",
    "dispatch_layer2_reflection",
]