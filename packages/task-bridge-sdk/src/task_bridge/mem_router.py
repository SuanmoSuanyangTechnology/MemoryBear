"""Task and queue contract shared by MemoryBear's producers and workers.

``memory-profile`` publishes by name; ``memory-jobs`` registers and consumes
those names. Both sides take the names, the queue and the routing table from
here, so a rename can never land on only one of them — a task published under a
name no worker registered simply sits in the queue until the caller's wait
budget runs out.

Ships with the ``mem-router`` extra::

    pip install "task-bridge-sdk[mem-router]"

The core SDK stays transport-neutral; this module is the one memory-specific
surface it carries, and it has no third-party dependencies.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class TaskName(StrEnum):
    """Tasks declared by the memory-jobs worker registry.

    Members spell the wire name without its ``memory_jobs.`` prefix:
    ``memory_jobs.<domain>.<action>`` → ``<DOMAIN>_<ACTION>``. The domain stays in
    the name so ``generate`` / ``scan`` cannot collide once further domains start
    publishing tasks.
    """

    #: Smokes the worker path end to end (no DB, no LLM).
    HEALTH_PING = "memory_jobs.health.ping"
    #: One user's insight + summary cache columns.
    PROFILE_CACHE_GENERATE = "memory_jobs.profile_cache.generate"
    #: Fan a whole workspace out into ``PROFILE_CACHE_GENERATE`` units.
    PROFILE_CACHE_GENERATE_WORKSPACE = "memory_jobs.profile_cache.generate_workspace"
    #: One user's interest distribution.
    INTEREST_DISTRIBUTION_GENERATE = "memory_jobs.interest_distribution.generate"
    #: Pre-warm: queue a per-user unit for every listed user without a cached one.
    INTEREST_DISTRIBUTION_INIT_FOR_USERS = "memory_jobs.interest_distribution.init_for_users"
    #: Periodic sweep that dispatches stale profile-cache units.
    PROFILE_CACHE_SCAN = "memory_jobs.profile_cache.scan"


class QueueName(StrEnum):
    """Broker queues owned by memory-jobs."""

    #: memory-jobs' own queue: every task above is registered and consumed here,
    #: so no other service's worker ever receives a task name it does not know.
    MEMORY_JOBS = "memory_jobs_tasks"


#: Highest priority number the queues are declared with (AMQP
#: ``x-max-priority``). Every app that declares ``memory_jobs_tasks`` — the
#: worker, both producers — must pass the same value or RabbitMQ rejects the
#: declaration with ``PRECONDITION_FAILED``.
MAX_PRIORITY = 9


class TaskPriority(IntEnum):
    """Message priorities on the AMQP (RabbitMQ) broker.

    AMQP priorities are a continuous scale where a **higher** number is more
    urgent (``kombu/entity.py``: "0 to broker configured max priority, where
    higher is better"), and they only order anything when the queue is declared
    with ``x-max-priority`` — otherwise RabbitMQ ignores the field
    (``kombu/entity.py``: "queues without a max priority set will ignore the
    priority field"). Three levels, one per urgency class; the numbers sit on
    that scale rather than being ranks.
    """

    #: A user is waiting on the response: single-user refresh, analytics.
    INTERACTIVE = 9
    #: Default for internal publishes: bulk work whose caller does not wait.
    NORMAL = 5
    #: Periodic sweeps and pre-warm dispatches; they yield to everything else.
    BACKGROUND = 0


#: Kombu's Redis priority buckets — Redis brokers only. Redis has no priority
#: field: kombu writes one list per (queue, step) and consumes them in ascending
#: order, so there the **lower** number runs first, the opposite of AMQP. These
#: steps are therefore meaningful only for a Redis broker; a deployment on
#: RabbitMQ (the supported target) ignores them.
PRIORITY_STEPS: tuple[int, ...] = (0, 3, 6, 9)


#: ``task name -> queue``. memory-jobs renders it into Celery's ``task_routes``
#: and producers publish with the queue this table names for their task. Every
#: declared task runs on the single owned queue; when one moves elsewhere,
#: replace this comprehension with explicit entries rather than deriving it.
TASK_ROUTES: dict[str, str] = {
    task.value: QueueName.MEMORY_JOBS.value for task in TaskName
}


__all__ = [
    "MAX_PRIORITY",
    "PRIORITY_STEPS",
    "QueueName",
    "TASK_ROUTES",
    "TaskName",
    "TaskPriority",
]
