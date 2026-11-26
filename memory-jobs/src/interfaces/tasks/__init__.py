"""Task registration for the Celery application.

Celery imports this package through ``include=["src.interfaces.tasks"]``; the
application also imports it eagerly so task routes and beat entries are known to
producers and to ``celery beat`` without relying on autodiscovery.

The package has three parts:

* ``registry.py`` — the one list of inbound jobs (wire name + handler factory);
* ``celery_adapter.py`` — renders that list into Celery tasks;
* ``schedules.py`` — the single management point for periodic entries.

Task names, the owned queue and the open-build ``task name -> queue`` table come
from ``task_bridge.mem_router`` — the same contract memory-profile publishes
with, so a rename cannot land on one side only. Edition-specific jobs, routes
and beat entries come from ``src.extensions``; every registered job must have a
route in the merged table, so a missing entry fails at import instead of
stranding publishes on the default queue.
"""

from task_bridge.mem_router import TASK_ROUTES as OPEN_BUILD_TASK_ROUTES

from src.bootstrap import get_settings
from src.extensions import extension_routes, load_extensions

# Imported for its registration side effect: every job in the registry becomes
# a task on the current Celery app.
from src.interfaces.tasks import celery_adapter  # noqa: F401
from src.interfaces.tasks.registry import get_jobs
from src.interfaces.tasks.schedules import build_beat_schedule

_settings = get_settings()
_extensions = load_extensions(_settings)

#: The merged ``task name -> queue`` table: open build first, extensions after.
TASK_ROUTES: dict[str, str] = {
    **dict(OPEN_BUILD_TASK_ROUTES),
    **extension_routes(_extensions),
}

_UNROUTED = {job.name for job in get_jobs()} - set(TASK_ROUTES)
if _UNROUTED:
    raise RuntimeError(
        "registered jobs without a route: "
        f"{sorted(_UNROUTED)}; add them to the extension's routes()"
    )

#: ``celery beat`` entries. Declared in ``schedules.py``; this is only the
#: rendered result for the current settings.
BEAT_SCHEDULE: dict[str, dict[str, object]] = build_beat_schedule(_settings)

__all__ = ["BEAT_SCHEDULE", "TASK_ROUTES"]
