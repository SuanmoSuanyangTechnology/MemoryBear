"""Single management point for periodic tasks (Celery beat).

Every recurring task in this service is declared in :func:`periodic_tasks` —
the one place to add, change or disable a schedule. ``src/tasks/__init__.py``
renders those declarations into ``celery_app.conf.beat_schedule`` through
:func:`build_beat_schedule`; nothing else writes beat entries.

Rules for a new periodic task:

1. Declare it in :func:`periodic_tasks` with a unique ``key``, bind its enable
   switch to a ``JobsSettings`` field rather than hardcoding ``True``, and pick
   a ``priority`` (sweeps are ``BACKGROUND``).
2. Keep it dispatch-only when it fans work out: the queued unit carries the
   real work, so a missed sweep needs no replay and ``expires`` can drop it.
3. It routes to this service's own queue (``DEFAULT_BEAT_QUEUE``); beat only
   publishes, workers on that queue execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from celery.schedules import BaseSchedule, crontab
from task_bridge.mem_router import QueueName, TaskName, TaskPriority

from src.bootstrap import get_settings
from src.config import JobsSettings

#: Beat only publishes; every entry therefore lands on this service's queue.
DEFAULT_BEAT_QUEUE = QueueName.MEMORY_JOBS


@dataclass(frozen=True, slots=True)
class PeriodicTask:
    """One declared beat entry, rendered into Celery's dict shape on demand."""

    key: str
    task: str
    schedule: BaseSchedule | float | int | timedelta
    enabled: bool = True
    #: Seconds after which a queued-but-unconsumed message is dropped. Safe for
    #: sweeping tasks: the next run re-enumerates everything anyway.
    expires: int | None = None
    queue: str = DEFAULT_BEAT_QUEUE.value
    #: Broker priority of the scheduled message itself (and, for dispatch-only
    #: tasks, the default their units should use). Sweeps belong at BACKGROUND.
    priority: int = TaskPriority.NORMAL

    def celery_entry(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "queue": self.queue,
            "priority": int(self.priority),
        }
        if self.expires is not None:
            options["expires"] = self.expires
        return {"task": self.task, "schedule": self.schedule, "options": options}


def periodic_tasks(settings: JobsSettings | None = None) -> tuple[PeriodicTask, ...]:
    """Every periodic task of this service, in registration order.

    Adding a periodic task means adding exactly one entry here.
    """

    settings = settings or get_settings()
    return (
        # Nightly sweep for stale insight/summary caches. Migrated from the
        # API's ``regenerate-memory-cache`` entry, which has been removed: this
        # service is now the only scanner. Same hour/minute and freshness window
        # env names (UTC, matching ``celery_app``'s timezone).
        PeriodicTask(
            key="profile-cache-scan",
            task=TaskName.PROFILE_CACHE_SCAN.value,
            schedule=crontab(
                hour=settings.memory_cache_regeneration_hour,
                minute=settings.memory_cache_regeneration_minute,
            ),
            enabled=settings.profile_cache_scan_enabled,
            expires=3600,
            # The sweep must never delay a user-triggered refresh.
            priority=TaskPriority.BACKGROUND,
        ),
    )


def build_beat_schedule(settings: JobsSettings | None = None) -> dict[str, dict[str, Any]]:
    """Render the enabled declarations into ``celery_app.conf.beat_schedule``."""

    settings = settings or get_settings()
    return {
        entry.key: entry.celery_entry()
        for entry in periodic_tasks(settings)
        if entry.enabled
    }
