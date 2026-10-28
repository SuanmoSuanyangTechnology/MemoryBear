"""Task module registry.

Celery imports this package through ``include=["src.tasks"]``; the application
also imports it eagerly so task routes and beat entries are known to producers
and to ``celery beat`` without relying on autodiscovery.

Layout: one package per task domain under ``src/tasks/``, one module per Celery
task inside it. Importing a package registers every task it declares, so the
import list below must stay explicit and alphabetical — a forgotten import is a
task that silently never registers.

Task names, the owned queue and the ``task name -> queue`` table come from
``task_bridge.mem_router`` — the same contract memory-profile publishes with, so
a rename cannot land on one side only. Each task module takes its ``name=`` from
``TaskName``; no literal task name is repeated in this service.

Periodic entries are not declared here: ``src/tasks/schedules.py`` is their
single management point, and this module only renders it.
"""

from task_bridge.mem_router import TASK_ROUTES

from src.bootstrap import get_settings
from src.tasks import (  # noqa: F401
    health,
    interest_distribution,
    profile_cache,
)
from src.tasks.schedules import build_beat_schedule

_settings = get_settings()

#: ``celery beat`` entries. Declared in ``src/tasks/schedules.py``; this is only
#: the rendered result for the current settings.
BEAT_SCHEDULE: dict[str, dict[str, object]] = build_beat_schedule(_settings)

__all__ = ["BEAT_SCHEDULE", "TASK_ROUTES"]
