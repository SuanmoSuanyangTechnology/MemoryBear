"""Delete storage-outbox rows past their retention window, in bounded batches.

The API's ``cleanup_outbox_events`` task deletes at most ``batch_size`` processed
rows and ``batch_size`` failed rows per call, then repeats while either window is
still full. The loop is capped at :data:`MAX_CLEANUP_ITERATIONS` so one run has a
bounded duration on a large backlog; the next scheduled run simply continues,
because deletion is idempotent and each call commits its own short transaction.

Processed and failed rows use separate windows (``retention_days`` and
``failed_retention_days``): a failure is the only trace of a projection that never
landed, so operators keep it longer.
"""

from __future__ import annotations

import logging

from src.application.ports.outbox.outbox import CleanupReport, OutboxStore

logger = logging.getLogger(__name__)

#: Hard cap of ``store.cleanup`` round-trips per run, mirroring the API task.
MAX_CLEANUP_ITERATIONS = 100


class CleanupOutboxUseCase:
    """Drain both retention windows, one bounded transaction per call."""

    def __init__(self, store: OutboxStore, *, batch_size: int) -> None:
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        self._store = store
        self._batch_size = batch_size

    def execute(self) -> CleanupReport:
        processed = 0
        failed = 0
        # Bounded task duration: each call commits its own short transaction.
        for _ in range(MAX_CLEANUP_ITERATIONS):
            counts = self._store.cleanup(self._batch_size)
            processed_batch = int(counts.get("processed", 0))
            failed_batch = int(counts.get("failed", 0))
            processed += processed_batch
            failed += failed_batch
            if processed_batch < self._batch_size and failed_batch < self._batch_size:
                break

        report = CleanupReport(processed=processed, failed=failed)
        logger.info(
            "outbox cleanup completed processed=%s failed=%s batch_size=%s",
            report.processed,
            report.failed,
            self._batch_size,
            extra={"event": "outbox_cleanup_completed"},
        )
        return report


__all__ = ["MAX_CLEANUP_ITERATIONS", "CleanupOutboxUseCase"]