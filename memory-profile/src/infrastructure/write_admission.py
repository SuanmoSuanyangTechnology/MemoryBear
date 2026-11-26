"""Publish the API's memory-write tasks through the dispatch admission.

The three tasks below are still registered and consumed by the API's workers
(``api/app/tasks.py``); this service only publishes them. Their queues belong to
the API as well, so each admitted message names its own queue instead of
following this service's routing table — the admission contract carries that
field precisely for a producer outside memory-jobs.

Admission gives the API scheduler's semantics without porting it: one queue,
one lock and one QPS window per ``(task_name, user_id)`` unit, a backlog
ceiling, and a producer-facing tracker. Two properties matter for the write
path and both survive: a user's messages run one at a time (the pipeline
depends on it), and a burst is refused rather than queued without bound.

QPS parity: the API resolves the write QPS from the tenant's plan
(``pre_user_memory_write_qps_limit``) and applies it to ``write_message`` only;
the fast and RAG tasks run unthrottled. This module applies the same rule, with
the value coming from memory-enterprise through the quota entitlements the
service already holds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis
from task_bridge import mem_admission
from task_bridge.mem_router import QueueName, TaskName, TaskPriority

logger = logging.getLogger(__name__)

#: Registered by ``api/app/tasks.py``; consumed on the API's write queue.
WRITE_MESSAGE_TASK = "app.core.memory.agent.write_message"
WRITE_MESSAGE_QUEUE = "memory_tasks"

#: Fast write (one dialogue node per user message); its own API queue.
FAST_WRITE_TASK = "app.core.memory.fast_write_message"
FAST_WRITE_QUEUE = "memory_fast_tasks"

#: RAG-space write (embedding + index upsert); its own API queue.
RAG_WRITE_TASK = "app.tasks.process_rag_memory"
RAG_WRITE_QUEUE = "memory_rag_tasks"

#: The tenant quota key the API's scheduler reads for the write QPS.
WRITE_QPS_LIMIT_KEY = "pre_user_memory_write_qps_limit"


@dataclass(frozen=True, slots=True)
class WriteTaskRule:
    """Where one admitted write task goes and what the gate enforces."""

    queue: str
    priority: int
    max_queue_len: int = mem_admission.DEFAULT_MAX_QUEUE_LEN


#: Task name -> routing and backlog ceiling. The API's scheduler keeps its
#: routing in ``task_routes`` and its backlog in ``SCHEDULER_MAX_QUEUE_LEN``;
#: this table is the same pairing for the three tasks this service publishes.
WRITE_TASK_RULES: dict[str, WriteTaskRule] = {
    WRITE_MESSAGE_TASK: WriteTaskRule(
        queue=WRITE_MESSAGE_QUEUE, priority=int(TaskPriority.NORMAL)
    ),
    FAST_WRITE_TASK: WriteTaskRule(
        queue=FAST_WRITE_QUEUE, priority=int(TaskPriority.INTERACTIVE)
    ),
    RAG_WRITE_TASK: WriteTaskRule(
        queue=RAG_WRITE_QUEUE, priority=int(TaskPriority.NORMAL)
    ),
}

#: Only the graph write is throttled, exactly like the API's QPS resolver table.
_THROTTLED_TASKS = frozenset({WRITE_MESSAGE_TASK})


class WriteAdmission:
    """Admit one write task and wake its dispatcher when the unit is ready."""

    def __init__(self, *, redis: Redis, task_bridge: object, entitlements: object) -> None:
        self._redis = redis
        self._task_bridge = task_bridge
        self._entitlements = entitlements

    async def write_qps(self, tenant_id: UUID | str | None) -> float:
        """The tenant's write QPS, or ``0`` (unlimited) when it cannot be read.

        The API degrades the same way: a missing tenant or a failed quota read
        leaves the gate open rather than failing the write.
        """

        if not tenant_id:
            return 0.0
        try:
            limits = await self._entitlements.effective_limits(tenant_id)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the gate must not fail the write
            logger.warning(
                "write QPS resolution failed; admitting unthrottled: tenant=%s error=%s",
                tenant_id,
                exc,
            )
            return 0.0
        value = limits.get(WRITE_QPS_LIMIT_KEY) if isinstance(limits, dict) else None
        try:
            return float(value) if value else 0.0
        except (TypeError, ValueError):
            return 0.0

    async def submit(
        self,
        task_name: str,
        *,
        user_id: str,
        params: dict,
        qps: float = 0.0,
    ) -> str:
        """Admit one task and return the admitted message id.

        The admitted id is the producer-facing tracker key — the same opaque id
        the API's ``/memory/write`` returns in ``data.task_ids``.
        """

        rule = WRITE_TASK_RULES[task_name]
        request = mem_admission.AdmissionRequest(
            task_name=task_name,
            user_id=user_id,
            params=dict(params),
            priority=rule.priority,
            max_queue_len=rule.max_queue_len,
            qps=qps if task_name in _THROTTLED_TASKS else 0.0,
            queue=rule.queue,
        )
        # One Lua script against the shared business Redis. The client this
        # service owns is asyncio-based, so the gate is awaited rather than
        # pushed to a thread — the script itself is a single round trip.
        outcome = await mem_admission.admit_async(self._redis, request)
        if outcome is mem_admission.AdmissionOutcome.ADMITTED_READY:
            # The unit was idle and this admission armed it; the contract
            # guarantees only one producer observes READY for one arming.
            await self._wake(request.unit_key)
        return request.msg_id

    async def _wake(self, unit_key: str) -> None:
        """Publish the dispatch signal for one armed unit.

        The wake goes through the memory-jobs bridge: it declares queues with
        ``x-max-priority`` exactly like the worker that owns the admission
        queue, which the API-facing publisher deliberately does not do.
        """

        try:
            await self._task_bridge.submit(  # type: ignore[attr-defined]
                TaskName.ADMISSION_DISPATCH.value,
                kwargs={"unit_key": unit_key},
                queue=QueueName.MEMORY_ADMISSION.value,
                priority=int(TaskPriority.NORMAL),
            )
        except Exception as exc:  # noqa: BLE001 - recovery sweep re-arms the unit
            logger.error(
                "admission wake failed; the recovery sweep will re-arm it: "
                "unit=%s error=%s",
                unit_key,
                exc,
            )


__all__ = [
    "FAST_WRITE_QUEUE",
    "FAST_WRITE_TASK",
    "RAG_WRITE_QUEUE",
    "RAG_WRITE_TASK",
    "WRITE_MESSAGE_QUEUE",
    "WRITE_MESSAGE_TASK",
    "WRITE_QPS_LIMIT_KEY",
    "WRITE_TASK_RULES",
    "WriteAdmission",
    "WriteTaskRule",
]
