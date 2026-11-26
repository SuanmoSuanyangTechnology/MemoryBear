"""Celery producer for identity-service.

identity runs no worker: task names and queues stay identical to the monolith
(and to application-service) so the existing notification-service worker
consumes them without changes. Publish retries are disabled at the reporter
boundary; a broker outage must degrade alerts, never the login path.
"""

from __future__ import annotations

from celery import Celery

from .config import settings

NOTIFICATION_STATE_QUEUE = "notification_state_tasks"


def build_celery_app() -> Celery:
    app = Celery("identity_service_tasks", broker=settings.celery_broker)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        task_default_queue=NOTIFICATION_STATE_QUEUE,
        task_ignore_result=True,
        broker_connection_retry_on_startup=True,
        task_publish_retry=False,
    )
    return app


celery_app = build_celery_app()
