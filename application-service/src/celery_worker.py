"""Celery worker entrypoint for workflow trigger tasks.

    celery -A src.celery_worker worker -Q workflow_trigger_tasks --pool=prefork

Every forked child assembles its own ProcessRuntime and runs all coroutines on
one persistent loop (see async_runtime). The parent process imports the task
modules so their names are registered.
"""

from __future__ import annotations

from celery.signals import worker_process_init, worker_process_shutdown

from . import tasks  # noqa: F401  (registers both task names)
from .celery_app import celery_app  # noqa: F401  (CLI target)

__all__ = ["celery_app"]


@worker_process_init.connect
def _init_worker_process(**_kwargs: object) -> None:
    from .assembly import assemble_process
    from .async_runtime import start_async_runtime

    assemble_process()
    start_async_runtime()


@worker_process_shutdown.connect
def _shutdown_worker_process(**_kwargs: object) -> None:
    from .async_runtime import stop_async_runtime

    stop_async_runtime()
