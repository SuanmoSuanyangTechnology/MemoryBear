"""Task module registry.

Celery imports this package through ``include=["src.tasks"]``; the application
also imports it eagerly so task routes and beat entries are known to producers
and to ``celery beat`` without relying on autodiscovery.

Import every task module below. A forgotten import is a task that silently
never registers, so keep this list explicit and alphabetical.
"""

from src.queues import QueueName
from src.tasks import health, interest_distribution, profile_cache  # noqa: F401

#: ``task name -> queue name``. Tasks without an entry land on the default
#: queue configured by ``JOBS_TASK_DEFAULT_QUEUE``.
TASK_ROUTES: dict[str, str] = {
    # Generation is CPU/LLM bound: keep it on the heavy queue next to the other
    # long-running memory work, and off the periodic scanner's queue.
    "memory_jobs.profile_cache.generate": QueueName.MEMORY_HEAVY.value,
    "memory_jobs.profile_cache.generate_workspace": QueueName.MEMORY_HEAVY.value,
    "memory_jobs.interest_distribution.generate": QueueName.MEMORY_HEAVY.value,
}

#: Celery beat entries. Each task module may add its own schedule entry here
#: or the registry can be extended inline once tasks exist.
BEAT_SCHEDULE: dict[str, dict[str, object]] = {}

__all__ = ["BEAT_SCHEDULE", "TASK_ROUTES"]
