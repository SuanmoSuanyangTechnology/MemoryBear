"""The inbound job port: what an executor runs, and how much it may use.

A job is the application's unit of scheduled work. The handler owns everything
that is not transport: it validates the decoded payload, calls the use case,
shapes the result and decides when a result counts as a failure. The executor
adapter owns the rest: decoding, the attempt token, resource limits,
acknowledgement and retries. Replacing Celery therefore means writing another
adapter, never touching a handler.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ResourceClass(StrEnum):
    """How much of a worker one job may occupy.

    A semantic class, not a number: every executor maps it onto its own limit
    mechanism (Celery time limits, a container CPU quota, a queue's concurrency
    reservation). The adapter owns the numbers, so a budget change stays out of
    the application.
    """

    #: No declared budget; the process-wide default applies.
    DEFAULT = "default"
    #: One short call, on an interactive latency budget.
    QUICK = "quick"
    #: Enumeration and dispatch: bounded by iteration, not by a model call.
    BULK = "bulk"
    #: A unit whose duration is set by the slowest model call.
    LLM_BOUND = "llm_bound"


@dataclass(frozen=True, slots=True)
class JobSpec:
    """Declarative execution policy for one job.

    ``retryable`` lets the executor replay the attempt after a transport
    failure; business failures remain the handler's decision. ``acks_late`` and
    ``store_result`` express the delivery and result expectations in broker
    terms rather than in one broker's option spelling.
    """

    resource_class: ResourceClass
    #: A lost run is safe and useful to repeat (the handler must be idempotent).
    retryable: bool = False
    #: Acknowledge after the handler returns, so a crashed worker redelivers.
    acks_late: bool = True
    #: Keep the handler's payload in the result backend.
    store_result: bool = True


class JobHandler(Protocol):
    """One named job's application behaviour.

    ``spec`` is a class attribute so an adapter can register the job without
    building its dependencies. ``handle`` receives the decoded payload and the
    opaque token the executor assigned to this attempt, and returns the
    application's own result object — serializing it for a caller is the
    adapter's job, not the handler's.
    """

    spec: JobSpec

    def handle(
        self, payload: Mapping[str, Any], *, attempt_token: str
    ) -> object: ...
