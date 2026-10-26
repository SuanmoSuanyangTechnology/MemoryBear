"""Celery application shared by workers, beat and producers.

The whole service is synchronous by design: tasks call blocking SQLAlchemy /
redis-py / elasticsearch-py / neo4j clients and never need to bridge an asyncio
event loop. That removes Celery's asyncio/fork interaction problems entirely.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_process_shutdown,
    worker_shutdown,
)

from src import tasks as task_registry
from src.bootstrap import get_settings
from src.common import get_logger, setup_logging
from src.common.context import clear_task_id, set_task_id
from src.runtime import get_runtime

# Bare names are parsed by Celery's Click CLI and would silently override the
# configured URLs. Only CELERY_BROKER_URL / CELERY_RESULT_BACKEND are supported.
for _legacy_env in ("BROKER_URL", "RESULT_BACKEND", "CELERY_BROKER", "CELERY_BACKEND"):
    os.environ.pop(_legacy_env, None)

settings = get_settings()
setup_logging(settings)

logger = get_logger(__name__)

celery_app = Celery(
    "memory_jobs",
    broker=settings.resolved_celery_broker_url,
    backend=settings.resolved_celery_result_backend,
    include=["src.tasks"],
)

celery_app.conf.update(
    # Serialization: JSON only, so every task argument is auditable.
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Observability
    task_track_started=True,
    task_ignore_result=False,
    worker_send_task_events=settings.worker_send_task_events,
    task_send_sent_event=settings.task_send_sent_event,
    # Limits. With acks_late a task killed by the hard limit is redelivered.
    task_time_limit=settings.task_time_limit_seconds,
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    # Delivery semantics
    task_acks_late=settings.task_acks_late,
    task_reject_on_worker_lost=settings.task_reject_on_worker_lost,
    worker_prefetch_multiplier=settings.worker_prefetch_multiplier,
    worker_disable_rate_limits=True,
    worker_redirect_stdouts_level="INFO",
    broker_connection_retry_on_startup=True,
    # Redis broker redelivers unacknowledged messages after this window. It
    # must stay above the hard time limit, otherwise a still-running task is
    # handed to a second worker.
    broker_transport_options={
        "visibility_timeout": settings.task_time_limit_seconds + 600,
    },
    result_expires=settings.result_expires_seconds,
    # Queues and schedules are declared by task modules through the registry.
    task_default_queue=settings.task_default_queue,
    task_routes=dict(task_registry.TASK_ROUTES),
    beat_schedule=dict(task_registry.BEAT_SCHEDULE),
)

# Imported for their side effect: registering tasks with this app. The registry
# import above already loads them, but keep the explicit call so a future switch
# to lazy autodiscovery does not change the boot behaviour.
celery_app.loader.import_default_modules()


@after_setup_logger.connect
@after_setup_task_logger.connect
def _configure_celery_logging(**_: object) -> None:
    """Keep a single structured handler after Celery installs its own."""

    setup_logging(settings, replace_existing=True)


@worker_process_init.connect
def _configure_worker_process(**_: object) -> None:
    """Give every forked child fresh clients before it serves a task."""

    runtime = get_runtime()
    # Never reuse sockets or pooled connections that were opened in the parent.
    runtime.reset_after_fork()
    if settings.startup_dependency_check:
        health = runtime.verify_dependencies()
        logger.info(
            "Worker process dependencies verified dependencies=%s",
            health,
            extra={"event": "worker_dependencies_verified"},
        )


@task_prerun.connect
def _bind_task_context(task_id: str | None = None, **_: object) -> None:
    set_task_id(task_id)


@task_postrun.connect
def _clear_task_context(**_: object) -> None:
    clear_task_id()


@worker_process_shutdown.connect
def _close_worker_process(**_: object) -> None:
    get_runtime().close()


@worker_shutdown.connect
def _close_worker(**_: object) -> None:
    get_runtime().close()
