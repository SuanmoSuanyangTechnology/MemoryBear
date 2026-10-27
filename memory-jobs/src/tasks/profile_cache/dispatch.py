"""Staggered enqueue of per-user cache units.

Shared by the workspace fan-out and the periodic sweep so both spread their
units across the same wave (``src.tasks.stagger``): 60 units per wave, 5s apart,
so a sweep does not burst the model service.
"""

from __future__ import annotations

from task_bridge.mem_router import TaskPriority

from src.tasks.profile_cache.generate import generate
from src.tasks.stagger import stagger_delay


def dispatch_generate(
    *,
    end_user_id: str,
    workspace_id: str,
    language: str,
    refresh_insight: bool,
    refresh_summary: bool,
    priority: int = TaskPriority.NORMAL,
    index: int = 0,
) -> None:
    """Publish one per-user unit; ``index`` positions it inside the stagger wave.

    ``priority`` is explicit at every call site: the sweep queues its units as
    ``BACKGROUND`` while a user-triggered fan-out queues them as ``NORMAL``.
    """

    generate.apply_async(
        kwargs={
            "end_user_id": end_user_id,
            "workspace_id": workspace_id,
            "language": language,
            "refresh_insight": refresh_insight,
            "refresh_summary": refresh_summary,
        },
        countdown=stagger_delay(index),
        priority=int(priority),
    )
