"""The memory router table is the producer/worker contract."""

from __future__ import annotations

from task_bridge.mem_router import (
    MAX_PRIORITY,
    PRIORITY_STEPS,
    TASK_ROUTES,
    QueueName,
    TaskName,
    TaskPriority,
)


def test_every_task_routes_to_a_declared_queue() -> None:
    assert set(TASK_ROUTES) == {task.value for task in TaskName}
    assert set(TASK_ROUTES.values()) <= {queue.value for queue in QueueName}


def test_priority_levels_fit_the_declared_queue_maximum() -> None:
    # AMQP rejects nothing for an out-of-range priority, it just ignores the
    # field — a level above x-max-priority would silently lose its urgency.
    assert all(0 <= int(level) <= MAX_PRIORITY for level in TaskPriority)
    assert len({int(level) for level in TaskPriority}) == len(TaskPriority)


def test_priority_levels_are_ordered_the_amqp_way() -> None:
    # RabbitMQ delivers the highest number first; the names must agree with the
    # numbers or a sweep outranks the user request it is supposed to yield to.
    assert TaskPriority.INTERACTIVE > TaskPriority.NORMAL > TaskPriority.BACKGROUND


def test_redis_bucket_steps_are_still_ascending() -> None:
    # Redis only, and the opposite polarity; kept for a Redis-broker fallback.
    assert list(PRIORITY_STEPS) == sorted(PRIORITY_STEPS)


def test_wire_literals_are_pinned() -> None:
    # A rename on one side alone leaves submissions unread; pin the literals so
    # changing them has to be deliberate in the worker's registry and manifests.
    assert TaskName.PROFILE_CACHE_GENERATE == "memory_jobs.profile_cache.generate"
    assert TaskName.PROFILE_CACHE_SCAN == "memory_jobs.profile_cache.scan"
    assert QueueName.MEMORY_JOBS == "memory_jobs_tasks"
