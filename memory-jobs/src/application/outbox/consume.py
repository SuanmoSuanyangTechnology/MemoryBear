"""Consume one bounded batch of storage-outbox rows serially.

This is the synchronous port of the API's ``consume_outbox_batch``. It leases a
row, renews the lease from a daemon thread while the projection is in flight, and
retries the projection *in place* up to :data:`MAX_ATTEMPTS` times, exactly like
the API, because the PostgreSQL lease — not Redis — is the single source of truth
during the two-phase rollout:

* a lease that stops being renewable means another consumer now owns the row, so
  the event ends ``lost`` and this worker stops projecting it immediately;
* a projector error is retried in place; the last attempt writes the terminal
  ``failed`` state, or ``lost`` when another consumer won the lease in between;
* a ``mark_processed`` that loses the race also ends ``lost``: Elasticsearch may
  already carry the document, and re-playing it would be wrong.

**Per-attempt wall-clock is not enforced here.** The API wraps each attempt in
``asyncio.timeout(min(60, processing_timeout / 2))``; a synchronous call cannot be
interrupted, so the only bound is the Elasticsearch/Neo4j client timeout the
projector is built with (plus the lease itself, which turns a slow projection
into a lost claim). :attr:`ConsumeOptions.attempt_timeout_seconds` is accepted
for configuration symmetry with the API and is used for the batch log/heartbeat
budget note only — it is deliberately not described as an enforced deadline.

The heartbeat thread performs a blocking ``store.heartbeat`` call. The store
opens one short transaction per call, so the renewal never shares a session with
the projection-side writes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from src.application.ports.outbox.outbox import (
    MAX_ATTEMPTS,
    ConsumeOptions,
    OutboxClaimLost,
    OutboxEvent,
    OutboxProjector,
    OutboxStore,
    ProjectionReport,
)

logger = logging.getLogger(__name__)

#: Upper bound of the lease-renewal cadence, matching the API's ``min(10, ...)``.
MAX_HEARTBEAT_INTERVAL_SECONDS = 10.0

#: Floor of the effective cadence. Only fakes with a tiny ``processing_timeout``
#: reach it; it keeps a misconfigured store from spinning the renewal thread.
MIN_HEARTBEAT_INTERVAL_SECONDS = 0.01


class _LeaseHeartbeat:
    """Renew one lease from a daemon thread and expose it as a claim probe.

    ``check`` is the ``check_claim`` callable handed to the projector: it raises
    :class:`OutboxClaimLost` as soon as the lease is gone (a failed renewal, a
    store error, or a renewal that already reported the loss). The thread exists
    only for the duration of one event; :meth:`stop` is always called by the use
    case's ``finally``.
    """

    def __init__(
        self,
        store: OutboxStore,
        event: OutboxEvent,
        interval_seconds: float,
    ) -> None:
        self._store = store
        self._event = event
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._renew,
            name=f"outbox-heartbeat-{event.id}",
            daemon=True,
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def check(self) -> None:
        """Renew the lease once; raise :class:`OutboxClaimLost` when it is gone."""

        if self._lost.is_set():
            raise OutboxClaimLost()
        try:
            owned = self._store.heartbeat(self._event.id, self._event.claim_token)
        except Exception:
            # A store error is indistinguishable from a lost lease for the
            # projection's purpose: stop touching the row instead of guessing.
            owned = False
        if not owned:
            self._lost.set()
            raise OutboxClaimLost()

    def _renew(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.check()
            except OutboxClaimLost:
                return

    def stop(self) -> None:
        """Signal the thread and wait briefly for it to leave the store call."""

        self._stop.set()
        if self._thread.is_alive():
            # The wait is bounded: a blocked driver call must not hang the batch.
            self._thread.join(timeout=max(1.0, min(5.0, self._interval * 2)))


class ConsumeOutboxBatchUseCase:
    """Lease and project at most ``options.batch_size`` rows, one at a time.

    Serial capacity is one, like the API: only the row currently being projected
    holds a lease, so a backlog never pins leases this worker cannot serve. One
    event's failure is counted and logged, never allowed to abort the rest of the
    batch.
    """

    def __init__(
        self,
        store: OutboxStore,
        projector: OutboxProjector,
        *,
        options: ConsumeOptions,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= options.batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        self._store = store
        self._projector = projector
        self._options = options
        self._clock = clock
        self._heartbeat_interval = _heartbeat_interval(store, options)

    def execute(self, worker_id: str) -> ProjectionReport:
        """Run one batch and return the same counter shape as the API task."""

        report = ProjectionReport()
        self._warm_up()
        report.expired = self._store.mark_expired_failed(self._options.batch_size)

        for _ in range(self._options.batch_size):
            try:
                claimed = self._store.claim_batch(worker_id, 1)
            except Exception:
                # A broken claim statement means the rest of the batch cannot be
                # leased either; report what happened so far instead of crashing.
                logger.error(
                    "outbox claim failed worker_id=%s",
                    worker_id,
                    exc_info=True,
                    extra={"event": "outbox_claim_failed"},
                )
                break
            if not claimed:
                break
            report.claimed += 1
            try:
                outcome = self._consume(claimed[0])
            except Exception:
                # Terminal-state write or attempt bookkeeping failed; the lease
                # (or the expired sweep) will clean the row up on a later run.
                logger.error(
                    "outbox event=%s ended without a terminal state",
                    claimed[0].id,
                    exc_info=True,
                    extra={"event": "outbox_event_error"},
                )
                outcome = "lost"
            report.add(outcome)

        self._log_batch(report)
        return report

    # ------------------------------------------------------------------ event

    def _consume(self, event: OutboxEvent) -> str:
        heartbeat = _LeaseHeartbeat(self._store, event, self._heartbeat_interval)
        heartbeat.start()
        try:
            return self._consume_claim(event, heartbeat)
        finally:
            heartbeat.stop()

    def _consume_claim(self, event: OutboxEvent, heartbeat: _LeaseHeartbeat) -> str:
        for _ in range(MAX_ATTEMPTS):
            if heartbeat.lost:
                return "lost"
            attempt = self._store.begin_attempt(event.id, event.claim_token)
            if attempt is None:
                return "lost"
            started = self._clock()
            try:
                # The API re-probes the lease before every attempt; a lease lost
                # between two attempts must not run another ES/Neo4j round-trip.
                heartbeat.check()
                self._projector.project(event, heartbeat.check)
            except OutboxClaimLost:
                logger.warning(
                    "outbox event=%s attempt=%s lease lost",
                    event.id,
                    attempt,
                    extra={"event": "outbox_lease_lost"},
                )
                return "lost"
            except Exception as exc:
                error = _safe_error(exc, self._store.error_max_length)
                elapsed = self._clock() - started
                if attempt >= MAX_ATTEMPTS:
                    updated = self._store.mark_failed(
                        event.id, event.claim_token, error
                    )
                    logger.error(
                        "outbox event=%s terminal=%s attempt=%s error=%s "
                        "elapsed_seconds=%.2f",
                        event.id,
                        "failed" if updated else "lost",
                        attempt,
                        error,
                        elapsed,
                        extra={"event": "outbox_event_terminal"},
                    )
                    return "failed" if updated else "lost"
                logger.warning(
                    "outbox event=%s attempt=%s error=%s elapsed_seconds=%.2f; "
                    "retry in place",
                    event.id,
                    attempt,
                    error,
                    elapsed,
                    extra={"event": "outbox_event_retry"},
                )
            else:
                # Elasticsearch may already carry the document. Never project
                # again just because the PostgreSQL acknowledgement failed.
                updated = self._store.mark_processed(event)
                return "processed" if updated else "lost"
        return "lost"

    # ------------------------------------------------------------------ batch

    def _warm_up(self) -> None:
        """Let the projector prepare its index state; failures stay per-event."""

        try:
            self._projector.warm_up()
        except Exception:
            logger.warning(
                "outbox projector warm-up failed",
                exc_info=True,
                extra={"event": "outbox_warm_up_failed"},
            )

    def _log_batch(self, report: ProjectionReport) -> None:
        if report.expired or report.lost:
            logger.error(
                "outbox lease failures expired=%s lost=%s",
                report.expired,
                report.lost,
                extra={"event": "outbox_lease_failures"},
            )
        logger.info(
            "outbox projection completed claimed=%s processed=%s failed=%s lost=%s "
            "expired=%s batch_size=%s attempt_timeout_seconds=%s",
            report.claimed,
            report.processed,
            report.failed,
            report.lost,
            report.expired,
            self._options.batch_size,
            self._options.attempt_timeout_seconds,
            extra={"event": "outbox_projection_completed"},
        )


def _heartbeat_interval(store: OutboxStore, options: ConsumeOptions) -> float:
    """The renewal cadence: the API's ``min(10, processing_timeout / 3)``.

    ``ConsumeOptions.heartbeat_interval_seconds`` may lower the cadence (a caller
    with a shorter store timeout), never raise it past the API's cap.
    """

    timeout = getattr(store, "processing_timeout", 0) or 0
    derived = (
        min(MAX_HEARTBEAT_INTERVAL_SECONDS, timeout / 3)
        if timeout > 0
        else MAX_HEARTBEAT_INTERVAL_SECONDS
    )
    configured = options.heartbeat_interval_seconds
    interval = min(derived, configured) if configured > 0 else derived
    return max(interval, MIN_HEARTBEAT_INTERVAL_SECONDS)


def _safe_error(exc: BaseException, max_length: int) -> str:
    """A bounded, diagnostic-only error text.

    Mirrors the API's ``safe_error``: only the exception type (and an HTTP status
    when the driver exposes one) is persisted. Driver messages can carry
    credentials, query text or documents, so they never reach the outbox row.
    """

    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        name += f" (HTTP {status})"
    return name[:max_length]


__all__ = [
    "MAX_HEARTBEAT_INTERVAL_SECONDS",
    "MIN_HEARTBEAT_INTERVAL_SECONDS",
    "ConsumeOutboxBatchUseCase",
]