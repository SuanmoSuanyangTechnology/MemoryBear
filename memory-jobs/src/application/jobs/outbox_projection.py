"""The storage-outbox projection job: one bounded consume batch.

The handler is the application's side of the task taken over from the API's
``app.tasks.scan_outbox_projection``: it runs one :class:`ConsumeOutboxBatchUseCase`
batch and renders the same counters the API task returns. ``attempt_token`` is the
worker id written on the lease: it is opaque to this layer, and it is bounded to
the column width (``locked_by`` is ``VARCHAR(128)``) before reaching the store.

The job is not retried by the executor: the outbox row itself carries the retry
budget (three in-place attempts), and re-running a whole batch after a transport
failure would only find the same pending rows with a fresh token.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.outbox.consume import ConsumeOutboxBatchUseCase
from src.application.ports.jobs import JobSpec, ResourceClass
from src.application.ports.outbox.outbox import ProjectionReport

#: ``memory_storage_outbox_events.locked_by`` is a 128-character column.
MAX_WORKER_ID_LENGTH = 128


class OutboxProjectionJob:
    """Lease and project one batch of storage-outbox events."""

    spec = JobSpec(resource_class=ResourceClass.BULK, acks_late=False)

    def __init__(self, use_case: ConsumeOutboxBatchUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, _payload: Mapping[str, Any], *, attempt_token: str
    ) -> ProjectionReport:
        return self._use_case.execute(
            worker_id=attempt_token[:MAX_WORKER_ID_LENGTH]
        )


__all__ = ["MAX_WORKER_ID_LENGTH", "OutboxProjectionJob"]