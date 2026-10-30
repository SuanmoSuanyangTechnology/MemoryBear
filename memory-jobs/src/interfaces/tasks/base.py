"""Base task class: retry and acknowledgement policy.

Tasks reach infrastructure only through their use cases (composition root); this
module carries no client accessors, so a task cannot open a session or a driver
outside the Unit of Work.
"""

from __future__ import annotations

from celery import Task
from sqlalchemy.exc import OperationalError


class JobTask(Task):
    """Base class for memory-jobs tasks.

    Retry policy is transport-only: ``OperationalError`` (connection lost,
    server restarting) is safe to replay, while business errors are not — a
    task must decide those itself. ``acks_late`` pairs with this: the message
    is acknowledged after the task returns, so a crashed worker redelivers it.
    Tasks must therefore be idempotent or guard their own side effects.
    """

    abstract = True

    autoretry_for = (OperationalError,)
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 5
    acks_late = True
