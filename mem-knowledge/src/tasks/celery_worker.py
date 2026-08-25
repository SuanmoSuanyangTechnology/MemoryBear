"""Celery worker loader and prefork lifecycle hooks."""

from __future__ import annotations

import logging

from celery.signals import worker_process_init, worker_process_shutdown

from ..bootstrap import get_settings
from ..logging import setup_logging
from ..runtime import reset_worker_runtime_after_fork, shutdown_worker_runtime_sync
from . import legacy_compat
from .celery_app import celery_app

logger = logging.getLogger(__name__)


@worker_process_init.connect
def initialize_worker_process(**kwargs: object) -> None:
    """Create fresh lazy resource owners after prefork."""

    del kwargs
    settings = get_settings()
    setup_logging(settings)
    runtime = reset_worker_runtime_after_fork()
    logger.info(
        "Knowledge worker process initialized pid=%s role=%s",
        runtime.pid,
        settings.kb_process_role,
    )


@worker_process_shutdown.connect
def shutdown_worker_process(**kwargs: object) -> None:
    """Release resources owned by the exiting worker process."""

    del kwargs
    shutdown_worker_runtime_sync()
    logger.info("Knowledge worker process stopped")


__all__ = ["celery_app", "legacy_compat"]
