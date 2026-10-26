"""Celery entrypoint.

Point the CLI at this module::

    celery -A src.entry:celery_app worker --pool=threads --queues=memory_tasks
    celery -A src.entry:celery_app beat
"""

from src.celery_app import celery_app

__all__ = ["celery_app"]
