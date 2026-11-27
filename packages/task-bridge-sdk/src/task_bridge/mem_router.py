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
    METADATA_MIGRATION_SCAN = "memory_jobs.metadata_migration.scan"
    METADATA_MIGRATION_DO = "memory_jobs.metadata_migration.do"
    RAG_PROFILE_GENERATE = "memory_jobs.dashboard.rag_profile_generate"
    EMOTION_SUGGESTIONS_GENERATE = "memory_jobs.emotion.suggestions_generate"
    FORGETTING_TRIGGER = "memory_jobs.forgetting.trigger"
    WORKING_MEMORY_DETAIL_GENERATE = "memory_jobs.working_memory.detail_generate"
    #: Dispatch one admitted unit (producer trigger or completion advance).
    ADMISSION_DISPATCH = "memory_jobs.admission.dispatch"
    #: Periodic sweep: expire dead attempts and wake units with queued work.
    ADMISSION_RECOVER = "memory_jobs.admission.recover"
    #: Periodic sweep projecting Neo4j memory-node changes into Elasticsearch.
    OUTBOX_PROJECTION = "memory_jobs.outbox.projection"
    #: Daily retention cleanup of terminal outbox rows.
    OUTBOX_CLEANUP = "memory_jobs.outbox.cleanup"
    #: A2 slice 1: persist one admitted write batch into ``memory_messages``.
    WRITE_INGEST = "memory_jobs.write.ingest"
    #: A2 S4a: dispatch one graph-write unit (pipeline shell).
    WRITE_GRAPH = "memory_jobs.write.graph"
    #: A2 S5c: write one RAG memory document (embedding + ES upsert).
    WRITE_RAG = "memory_jobs.write.rag"
    #: A2 S7a: 1 message -> 1 :Dialogue node (clean + embedding + MERGE).
    WRITE_FAST = "memory_jobs.write.fast"
    #: Incrementally assign newly extracted entities to communities.
    COMMUNITY_CLUSTER_INCREMENTAL = "memory_jobs.community.cluster_incremental"

    #: Layer 2 reflection: periodic sweep that dispatches ``REFLECTION_LAYER2``
    #: for every active user whose ``end_users.reflection_time`` is older than
    #: the workspace config's ``iteration_period``.
    REFLECTION_LAYER2_SCAN = "memory_jobs.reflection.layer2_scan"
    #: One user's Layer 2 inspection (unresolved entities, alias merge, entity
    #: dedup, metadata extraction, description merge), behind the per-user write
    #: lock.
    REFLECTION_LAYER2 = "memory_jobs.reflection.layer2"
    #: Periodic sweep that dispatches ``REFLECTION_DEDUP`` (low-frequency full
    #: scan dedup) once a day.
    REFLECTION_DEDUP_SCAN = "memory_jobs.reflection.dedup_scan"
    #: One user's full-scan entity dedup, grouped by entity type.
    REFLECTION_DEDUP = "memory_jobs.reflection.dedup"
    #: Periodic sweep of the reflection retry ZSets: re-dispatches users whose
    #: backoff elapsed and reaps workers whose lease expired.
    REFLECTION_RETRY_SCAN = "memory_jobs.reflection.retry_scan"
    #: Workspace-wide reflection over app/config/end-user triples, published by
    #: memory-profile on behalf of the profile front.
    REFLECTION_WORKSPACE = "memory_jobs.reflection.workspace"
    #: One trial run of the conflict-detection reflection engine for a config,
    #: published on behalf of the profile front.
    REFLECTION_RUN = "memory_jobs.reflection.run"


class EnterpriseTaskName(StrEnum):
    """Tasks registered only by the enterprise memory-jobs worker.

    The wire names stay in this contract-only package so the producer and the
    worker cannot drift, but no open build registers them. The enterprise
    package ``enterprise_ext.memory_jobs`` contributes these tasks, their
    routes and their handlers through ``JOBS_EXTENSION_MODULES``.

    Prompt-workbench units run on their own queue so a long GEPA search or a
    large answer batch cannot starve memory tasks.
    """

    PROMPT_WORKBENCH_VARIABLES_IMPORT = "memory_jobs.prompt_workbench.variables_import"
    PROMPT_WORKBENCH_CASES_GENERATE = "memory_jobs.prompt_workbench.cases_generate"
    PROMPT_WORKBENCH_ANSWERS_GENERATE = "memory_jobs.prompt_workbench.answers_generate"
    PROMPT_WORKBENCH_REVIEWS_GENERATE = "memory_jobs.prompt_workbench.reviews_generate"
    PROMPT_WORKBENCH_OPTIMIZE = "memory_jobs.prompt_workbench.optimize"


class QueueName(StrEnum):
    """Broker queues owned by memory-jobs."""

#: memory-jobs ordinary queue (migration do has its own queue):
    #: so no other service's worker ever receives a task name it does not know.
    MEMORY_JOBS = "memory_jobs_tasks"
    METADATA_MIGRATION = "metadata_migration_tasks"
    #: Storage-outbox projection queue. The scan runs every few seconds and the
    #: projection calls Neo4j/Elasticsearch, so it gets its own worker instead of
    #: competing with memory work for the same threads.
    MEMORY_PROJECTION = "memory_projection_tasks"
    #: Prompt-workbench execution queue. Deploy a separate worker for it; the
    #: units are model-bound and must not share a thread pool with memory work.
    PROMPT_WORKBENCH = "prompt_workbench_tasks"
    #: Dispatch-admission queue. Its units only wake a unit or finish an
    #: attempt, so they must not queue behind the work they are admitting.
    MEMORY_ADMISSION = "memory_admission_tasks"


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


#: Tasks that run on the dispatch-admission queue instead of the work queue.
_ADMISSION_TASKS = frozenset(
    {
        TaskName.ADMISSION_DISPATCH.value,
        TaskName.ADMISSION_RECOVER.value,
    }
)

#: Tasks that run on the storage-outbox projection queue.
_PROJECTION_TASKS = frozenset(
    {
        TaskName.OUTBOX_PROJECTION.value,
        TaskName.OUTBOX_CLEANUP.value,
    }
)


def _queue_for(task: TaskName) -> str:
    if task.value in _ADMISSION_TASKS:
        return QueueName.MEMORY_ADMISSION.value
    if task.value in _PROJECTION_TASKS:
        return QueueName.MEMORY_PROJECTION.value
    return QueueName.MEMORY_JOBS.value


#: ``task name -> queue`` for the open build. memory-jobs renders it into
#: Celery's ``task_routes`` and producers publish with the queue this table
#: names for their task. Enterprise-only tasks add their entries through
#: ``src.extensions``; their queue names still come from :class:`QueueName`.
#: Migration do is explicitly routed to its dedicated queue.
TASK_ROUTES: dict[str, str] = {task.value: _queue_for(task) for task in TaskName}
TASK_ROUTES[TaskName.METADATA_MIGRATION_DO.value] = QueueName.METADATA_MIGRATION.value


__all__ = [
    "MAX_PRIORITY",
    "PRIORITY_STEPS",
    "EnterpriseTaskName",
    "QueueName",
    "TASK_ROUTES",
    "TaskName",
    "TaskPriority",
]





