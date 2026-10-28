"""Staggered enqueue of per-user interest-distribution units.

Shared by the dashboard pre-warm so a workspace full of users reaches the model
service at the wave rate ``src.tasks.stagger`` defines.
"""

from __future__ import annotations

from task_bridge.mem_router import TaskPriority

from src.tasks.interest_distribution.generate import generate
from src.tasks.stagger import stagger_delay


def dispatch_generate(
    *,
    end_user_id: str,
    workspace_id: str,
    language: str,
    limit: int,
    write_cache: bool,
    priority: int = TaskPriority.NORMAL,
    index: int = 0,
) -> None:
    """Publish one per-user unit; ``index`` positions it inside the stagger wave.

    ``write_cache`` is explicit: the online path leaves the cache to
    memory-profile while the pre-warm has no caller, so it asks the unit to
    write the payload itself.
    """

    generate.apply_async(
        kwargs={
            "end_user_id": end_user_id,
            "workspace_id": workspace_id,
            "language": language,
            "limit": limit,
            "write_cache": write_cache,
        },
        countdown=stagger_delay(index),
        priority=int(priority),
    )
