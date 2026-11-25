"""Celery app owned by the application-service worker and beat.

Task names and queues are unchanged from the monolith
(``app.tasks.*`` -> ``workflow_trigger_tasks``) so the worker/beat handover is
transparent to operators and to schedules already in flight. Config values
mirror the monolith so a task behaves the same on either consumer.
"""

from __future__ import annotations

from celery import Celery

from .bootstrap import get_settings

WORKFLOW_TRIGGER_QUEUE = "workflow_trigger_tasks"


def build_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "application_service_tasks",
        broker=settings.celery_broker,
        backend=settings.celery_result_backend_url,
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_track_started=True,
        task_ignore_result=False,
        task_time_limit=3600,
        task_soft_time_limit=3000,
        worker_prefetch_multiplier=1,
        result_expires=3600,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_disable_rate_limits=True,
        task_default_queue=WORKFLOW_TRIGGER_QUEUE,
        task_routes={
            "app.tasks.scan_workflow_schedule_triggers": {
                "queue": WORKFLOW_TRIGGER_QUEUE
            },
            "app.tasks.run_workflow_schedule_trigger": {
                "queue": WORKFLOW_TRIGGER_QUEUE
            },
        },
    )
    return app


celery_app = build_celery_app()
