"""Celery adapter for the registered inbound jobs.

This is the only module that knows how a job becomes a Celery task: the wire
name, the budget of its resource class, the acknowledgement and result policy,
and the attempt token handed to the handler. Iterating ``registry.JOBS`` means
a new job needs no Celery code — add the handler and its registry entry.

The adapter also owns the transport envelope: a handler returns an application
result object, ``serialization`` renders the wire shape, and the task adds
``task_id`` (the attempt's identity) before answering.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from celery import shared_task

from src.application.ports.jobs import JobSpec, ResourceClass
from src.interfaces.tasks.base import JobTask
from src.interfaces.tasks.registry import JOBS, JobRegistration
from src.interfaces.tasks.serialization import serialize

#: Resource class -> (hard limit, soft limit), in seconds. ``None`` leaves the
#: process-wide limits in ``celery_app`` in force. The API used these same
#: budgets for the same units, so the numbers are a compatibility contract.
_RESOURCE_LIMITS: dict[ResourceClass, tuple[int | None, int | None]] = {
    ResourceClass.DEFAULT: (None, None),
    ResourceClass.QUICK: (180, 150),
    ResourceClass.BULK: (600, 540),
    ResourceClass.LLM_BOUND: (900, 840),
}


def _task_options(spec: JobSpec) -> dict[str, Any]:
    """Translate the semantic spec into Celery task options."""

    hard, soft = _RESOURCE_LIMITS[spec.resource_class]
    options: dict[str, Any] = {
        "bind": True,
        "base": JobTask,
        "acks_late": spec.acks_late,
        "ignore_result": not spec.store_result,
    }
    if not spec.retryable:
        # ``JobTask`` retries ``OperationalError``; a non-retryable job still
        # carries the policy but never spends an attempt on it.
        options["max_retries"] = 0
    if hard is not None:
        options["time_limit"] = hard
        options["soft_time_limit"] = soft
    return options


def _job_runner(registration: JobRegistration) -> Callable[..., dict[str, Any]]:
    """The generic task body: build the handler, run it, add the envelope."""

    def run(self: JobTask, **payload: Any) -> dict[str, Any]:
        handler = registration.build()
        response = serialize(
            handler.handle(payload, attempt_token=str(self.request.id or ""))
        )
        response["task_id"] = self.request.id
        return response

    # The wire name is explicit; a readable function name only helps traces.
    run.__name__ = registration.name.replace(".", "_")
    run.__qualname__ = run.__name__
    run.__doc__ = f"Run the ``{registration.name}`` job through its handler."
    return run


def _register(registration: JobRegistration) -> Any:
    return shared_task(
        name=registration.name, **_task_options(registration.spec)
    )(_job_runner(registration))


#: Every registered job as a Celery task, keyed by its wire name.
TASKS: dict[str, Any] = {
    registration.name: _register(registration) for registration in JOBS
}
