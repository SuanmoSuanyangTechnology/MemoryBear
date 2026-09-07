"""Lightweight task observability primitives for knowledge workers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self

_OBSERVABILITY_LOGGER = logging.getLogger("knowledge.tasks.observability")
_MAIN_PID = os.getpid()
_TASK_TIMING_LOCK = threading.Lock()
_TASK_PRERUN_TIMING: dict[str, tuple[int, int]] = {}
_CHILD_TASK_INDEX = 0
_SAFE_HEADER_TEXT = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class BusinessOutcome(StrEnum):
    """Business terminal states independent from Celery task states."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL_FAILURE = "partial_failure"
    RETRY = "retry"
    SKIPPED = "skipped"
    COALESCED = "coalesced"
    ABORTED = "aborted"
    REVOKED = "revoked"


@dataclass(frozen=True)
class TaskContext:
    """Safe task identity propagated to all observability events."""

    service: str
    role: str
    hostname: str
    main_pid: int
    pid: int
    task_name: str
    task_id: str
    queue: str | None
    attempt: int
    knowledge_id: str | None = None
    document_id: str | None = None
    trace_id: str | None = None
    parent_task_id: str | None = None
    published_at_ms: int | None = None
    received_at_ms: int | None = None
    started_at_ms: int | None = None
    cold_start: bool = False
    child_task_index: int = 0


@dataclass(frozen=True)
class TaskEvent:
    """One immutable worker or task event."""

    event: str
    context: TaskContext
    stage: str | None = None
    detail: str | None = None
    business_outcome: BusinessOutcome | None = None
    celery_outcome: str | None = None
    duration_ms: int | None = None
    queue_wait_ms: int | None = None
    wait_duration_ms: int | None = None
    retry_in_ms: int | None = None
    progress: float | None = None
    error_code: str | None = None
    error_type: str | None = None
    error_fingerprint: str | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    display_message: str | None = None
    force_flush: bool = False
    exception: BaseException | None = field(default=None, repr=False, compare=False)


class TaskEventSink(Protocol):
    """Consume task events without changing task behavior."""

    def emit(self, event: TaskEvent) -> None: ...


TASK_LOG_FIELDS = (
    "event",
    "service",
    "role",
    "hostname",
    "main_pid",
    "pid",
    "task_name",
    "task_id",
    "queue",
    "attempt",
    "trace_id",
    "parent_task_id",
    "knowledge_id",
    "document_id",
    "stage",
    "detail",
    "business_outcome",
    "celery_outcome",
    "duration_ms",
    "queue_wait_ms",
    "wait_duration_ms",
    "retry_in_ms",
    "progress",
    "error_code",
    "error_type",
    "error_fingerprint",
)

TASK_COUNT_FIELDS = (
    "discovered",
    "created",
    "updated",
    "unchanged",
    "deleted",
    "parse_dispatched",
    "failed",
    "documents",
    "chunks",
    "batches",
)


class StructuredLogSink:
    """Emit one allowlisted key-value log line per task event."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("knowledge.tasks")

    @staticmethod
    def _encoded(value: object) -> str:
        if isinstance(value, StrEnum):
            value = value.value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _fields(event: TaskEvent) -> dict[str, object]:
        context = event.context
        fields: dict[str, object] = {
            "event": event.event,
            "service": context.service,
            "role": context.role,
            "hostname": context.hostname,
            "main_pid": context.main_pid,
            "pid": context.pid,
            "task_name": context.task_name,
            "task_id": context.task_id,
            "queue": context.queue,
            "attempt": context.attempt,
            "trace_id": context.trace_id,
            "parent_task_id": context.parent_task_id,
            "knowledge_id": context.knowledge_id,
            "document_id": context.document_id,
            "stage": event.stage,
            "detail": event.detail,
            "business_outcome": event.business_outcome,
            "celery_outcome": event.celery_outcome,
            "duration_ms": event.duration_ms,
            "queue_wait_ms": event.queue_wait_ms,
            "wait_duration_ms": event.wait_duration_ms,
            "retry_in_ms": event.retry_in_ms,
            "progress": event.progress,
            "error_code": event.error_code,
            "error_type": event.error_type,
            "error_fingerprint": event.error_fingerprint,
        }
        for key in TASK_COUNT_FIELDS:
            value = event.counts.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                fields[f"count_{key}"] = value
        return {key: value for key, value in fields.items() if value is not None}

    def emit(self, event: TaskEvent) -> None:
        fields = self._fields(event)
        message = " ".join(
            f"{key}={self._encoded(value)}"
            for key, value in fields.items()
        )
        level = logging.ERROR if event.event in {
            "kb_task_failed",
            "kb_task_implementation_error",
        } else logging.INFO
        exc_info = (
            (type(event.exception), event.exception, event.exception.__traceback__)
            if event.exception is not None
            else None
        )
        self._logger.log(level, message, exc_info=exc_info)


class DocumentProgressSink:
    """Persist throttled task progress through short database sessions."""

    _TERMINAL_EVENTS = {"kb_task_finished", "kb_task_failed"}
    _ABORTED_MESSAGE = "Task aborted (deleted or cancelled)."

    def __init__(
        self,
        *,
        runtime: object,
        document_id: str,
        flush_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        try:
            self._document_id = uuid.UUID(str(document_id))
        except (TypeError, ValueError):
            self._document_id = None
        self._flush_interval_seconds = flush_interval_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._last_flush_at: float | None = None
        self._pending_messages: list[str] = []

    @staticmethod
    def _append_message(current: str | None, messages: Sequence[str]) -> str:
        current_text = current.rstrip("\n") if current else ""
        pending_text = "\n".join(message.rstrip("\n") for message in messages if message)
        # Legacy processors may persist the complete snapshot before terminal flush.
        if pending_text and f"\n{pending_text}\n" not in f"\n{current_text}\n":
            current_text = "\n".join(text for text in (current_text, pending_text) if text)
        return f"{current_text}\n" if current_text else ""

    def _should_flush(self, event: TaskEvent, now: float) -> bool:
        return (
            event.force_flush
            or event.event in self._TERMINAL_EVENTS
            or self._last_flush_at is None
            or now - self._last_flush_at >= self._flush_interval_seconds
        )

    def emit(self, event: TaskEvent) -> None:
        if self._document_id is None:
            return
        if (
            event.event not in self._TERMINAL_EVENTS
            and event.event != "kb_task_started"
            and event.progress is None
            and not event.display_message
        ):
            return
        now = self._clock()
        with self._lock:
            if event.display_message:
                self._pending_messages.append(event.display_message)
            elif (
                event.event == "kb_task_finished"
                and event.business_outcome is BusinessOutcome.ABORTED
            ):
                self._pending_messages.append(self._ABORTED_MESSAGE)
            if not self._should_flush(event, now):
                return
            pending_messages = tuple(self._pending_messages)
            self._persist(event, pending_messages)
            self._pending_messages.clear()
            self._last_flush_at = now

    def _persist(self, event: TaskEvent, messages: Sequence[str]) -> None:
        from ..models.owned import Document
        from ..utils.datetime_utils import utcnow_naive

        database = self._runtime.database
        with database.sync_session() as session:
            document = session.get(Document, self._document_id)
            if document is None:
                return
            if event.event == "kb_task_started":
                document.run = 1
                document.progress = 0.0
                document.process_begin_at = utcnow_naive()
            elif event.event == "kb_task_failed":
                document.run = 0
                document.progress = -1.0
            elif event.event == "kb_task_finished":
                document.run = 0
                if event.business_outcome is BusinessOutcome.SUCCESS:
                    document.progress = 1.0
            else:
                document.run = 1
                if event.progress is not None:
                    document.progress = float(event.progress)
            if messages:
                document.progress_msg = self._append_message(
                    document.progress_msg,
                    messages,
                )
            session.commit()


class CeleryStateSink:
    """Expose active task stages without replacing Celery terminal states."""

    _ACTIVE_EVENTS = {
        "kb_task_started",
        "kb_task_stage_started",
        "kb_task_stage_finished",
        "kb_task_progress",
    }

    def __init__(self, update_state: Callable[..., None], *, task_id: str) -> None:
        self._update_state = update_state
        self._task_id = task_id

    def emit(self, event: TaskEvent) -> None:
        if event.event not in self._ACTIVE_EVENTS:
            return
        meta: dict[str, object] = {}
        for key in (
            "stage",
            "detail",
            "duration_ms",
            "wait_duration_ms",
            "progress",
        ):
            value = getattr(event, key)
            if value is not None:
                meta[key] = value
        for key in TASK_COUNT_FIELDS:
            value = event.counts.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                meta[f"count_{key}"] = value
        if not meta:
            return
        self._update_state(task_id=self._task_id, state="STARTED", meta=meta)


def error_fingerprint(exc: BaseException) -> str:
    """Group failures by exception type and stable Knowledge source frames."""

    frames = traceback.extract_tb(exc.__traceback__)
    normalized_frames = [
        f"{frame.filename.rsplit('/mem-knowledge/src/', 1)[-1]}:{frame.lineno}:{frame.name}"
        for frame in frames
        if "/mem-knowledge/src/" in frame.filename
    ]
    source = "|".join([type(exc).__name__, *normalized_frames[-5:]])
    return hashlib.sha256(source.encode()).hexdigest()[:16]


class TaskRun(AbstractContextManager["TaskRun"]):
    """Track one task's stages and exactly one business terminal state."""

    def __init__(
        self,
        context: TaskContext,
        *,
        sinks: Sequence[TaskEventSink],
        heartbeat_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.context = context
        self._sinks = tuple(sinks)
        self._heartbeat_seconds = heartbeat_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._started_at = clock()
        self._terminal = False
        self._current_stage: str | None = None
        self._current_stage_started_at: float | None = None
        self._failed_stage: str | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def _emit(self, event: TaskEvent) -> None:
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception as exc:  # noqa: BLE001 - observability must not alter task behavior.
                _OBSERVABILITY_LOGGER.error(
                    "event=kb_task_observability_sink_failed sink_type=%s "
                    "error_type=%s source_event=%s",
                    type(sink).__name__,
                    type(exc).__name__,
                    event.event,
                )

    @staticmethod
    def _validated_counts(counts: Mapping[str, int] | None) -> dict[str, int]:
        values = dict(counts or {})
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values.values()):
            raise ValueError("progress counts must contain integer values")
        return values

    def __enter__(self) -> Self:
        queue_wait_ms = None
        if self.context.published_at_ms is not None and self.context.started_at_ms is not None:
            queue_wait_ms = max(0, self.context.started_at_ms - self.context.published_at_ms)
        self._emit(
            TaskEvent(
                event="kb_task_started",
                context=self.context,
                queue_wait_ms=queue_wait_ms,
            )
        )
        if self._heartbeat_seconds > 0:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"kb-task-heartbeat-{self.context.task_id[-8:]}",
                daemon=True,
            )
            self._heartbeat_thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, traceback
        try:
            if exc is not None and not self._terminal:
                self.finish(BusinessOutcome.FAILURE, exc=exc)
            elif exc is None and not self._terminal:
                self.finish(
                    BusinessOutcome.FAILURE,
                    error_code="KB_TASK_TERMINAL_MISSING",
                    detail="task_returned_without_business_terminal",
                )
        finally:
            self._heartbeat_stop.set()
            heartbeat_thread = self._heartbeat_thread
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=max(1.0, self._heartbeat_seconds * 2))
        return False

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self._heartbeat_seconds):
            with self._lock:
                if self._terminal:
                    continue
                stage = self._current_stage
                stage_started_at = self._current_stage_started_at
            if stage is None or stage_started_at is None:
                continue
            self._emit(
                TaskEvent(
                    event="kb_task_progress",
                    context=self.context,
                    stage=stage,
                    detail="heartbeat",
                    wait_duration_ms=max(
                        0,
                        int(round((self._clock() - stage_started_at) * 1000)),
                    ),
                )
            )

    @contextmanager
    def stage(self, name: str):
        started_at = self._clock()
        with self._lock:
            previous_stage = self._current_stage
            previous_stage_started_at = self._current_stage_started_at
            self._current_stage = name
            self._current_stage_started_at = started_at
        self._emit(TaskEvent(event="kb_task_stage_started", context=self.context, stage=name))
        try:
            yield
        except BaseException:
            with self._lock:
                self._failed_stage = name
            raise
        finally:
            with self._lock:
                self._current_stage = previous_stage
                self._current_stage_started_at = previous_stage_started_at
            duration_ms = max(0, int(round((self._clock() - started_at) * 1000)))
            self._emit(
                TaskEvent(
                    event="kb_task_stage_finished",
                    context=self.context,
                    stage=name,
                    duration_ms=duration_ms,
                )
            )

    def progress(
        self,
        *,
        stage: str,
        fraction: float | None,
        detail: str,
        display_message: str | None = None,
        counts: Mapping[str, int] | None = None,
        duration_ms: int | None = None,
        wait_duration_ms: int | None = None,
        force: bool = False,
    ) -> None:
        if (
            fraction is not None
            and (
                isinstance(fraction, bool)
                or not isinstance(fraction, (int, float))
                or not 0.0 <= fraction <= 1.0
            )
        ):
            raise ValueError("progress fraction must be between 0 and 1")
        if wait_duration_ms is not None and (
            not isinstance(wait_duration_ms, int)
            or isinstance(wait_duration_ms, bool)
            or wait_duration_ms < 0
        ):
            raise ValueError("wait duration must be a non-negative integer")
        if duration_ms is not None and (
            not isinstance(duration_ms, int)
            or isinstance(duration_ms, bool)
            or duration_ms < 0
        ):
            raise ValueError("stage duration must be a non-negative integer")
        self._emit(
            TaskEvent(
                event="kb_task_progress",
                context=self.context,
                stage=stage,
                detail=detail,
                progress=fraction,
                duration_ms=duration_ms,
                wait_duration_ms=wait_duration_ms,
                counts=self._validated_counts(counts),
                display_message=display_message,
                force_flush=force,
            )
        )

    def finish(
        self,
        outcome: BusinessOutcome,
        *,
        error_code: str | None = None,
        exc: BaseException | None = None,
        detail: str | None = None,
        counts: Mapping[str, int] | None = None,
    ) -> None:
        with self._lock:
            if self._terminal:
                self._emit(
                    TaskEvent(
                        event="kb_task_implementation_error",
                        context=self.context,
                        detail="duplicate_business_terminal",
                        error_code=error_code,
                    )
                )
                return
            self._terminal = True
        event_name = {
            BusinessOutcome.FAILURE: "kb_task_failed",
            BusinessOutcome.RETRY: "kb_task_retry",
        }.get(outcome, "kb_task_finished")
        self._emit(
            TaskEvent(
                event=event_name,
                context=self.context,
                stage=(
                    self._failed_stage
                    if outcome in {BusinessOutcome.FAILURE, BusinessOutcome.RETRY}
                    else None
                ),
                detail=detail,
                business_outcome=outcome,
                duration_ms=max(0, int(round((self._clock() - self._started_at) * 1000))),
                error_code=error_code,
                error_type=type(exc).__name__ if exc is not None else None,
                error_fingerprint=error_fingerprint(exc) if exc is not None else None,
                counts=self._validated_counts(counts),
                exception=exc,
            )
        )


def observe_task(
    context: TaskContext,
    *,
    sinks: Sequence[TaskEventSink],
    heartbeat_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> TaskRun:
    """Create one task run without initializing external resources."""

    return TaskRun(
        context,
        sinks=sinks,
        heartbeat_seconds=heartbeat_seconds,
        clock=clock,
    )


def current_parent_task_id() -> str | None:
    """Return the current Celery task ID when dispatching a child task."""

    try:
        from celery import current_task
    except ImportError:
        return None
    request = getattr(current_task, "request", None)
    task_id = getattr(request, "id", None)
    return str(task_id) if task_id else None


def reset_observability_after_fork() -> None:
    """Reset child-local timing without changing the inherited main PID."""

    global _CHILD_TASK_INDEX
    with _TASK_TIMING_LOCK:
        _CHILD_TASK_INDEX = 0
        _TASK_PRERUN_TIMING.clear()


def record_task_prerun(task_id: str, *, started_at_ms: int | None = None) -> None:
    """Capture task start before the task envelope imports business modules."""

    global _CHILD_TASK_INDEX
    if not task_id:
        return
    with _TASK_TIMING_LOCK:
        _CHILD_TASK_INDEX += 1
        _TASK_PRERUN_TIMING[str(task_id)] = (
            started_at_ms if started_at_ms is not None else int(time.time() * 1000),
            _CHILD_TASK_INDEX,
        )


def _consume_task_prerun(task_id: str) -> tuple[int, int]:
    global _CHILD_TASK_INDEX
    with _TASK_TIMING_LOCK:
        timing = _TASK_PRERUN_TIMING.pop(task_id, None)
        if timing is not None:
            return timing
        _CHILD_TASK_INDEX += 1
        return int(time.time() * 1000), _CHILD_TASK_INDEX


def _safe_uuid(value: object | None) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _safe_header_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if _SAFE_HEADER_TEXT.fullmatch(text) else None


def task_context_from_current_task(
    *,
    role: str,
    knowledge_id: object | None = None,
    document_id: object | None = None,
) -> TaskContext:
    """Build a safe task context from Celery's current task proxy."""

    from celery import current_task

    request = getattr(current_task, "request", None)
    task_id = str(getattr(request, "id", None) or "unknown")
    started_at_ms, child_task_index = _consume_task_prerun(task_id)
    headers = getattr(request, "headers", None)
    headers = headers if isinstance(headers, Mapping) else {}
    delivery_info = getattr(request, "delivery_info", None)
    delivery_info = delivery_info if isinstance(delivery_info, Mapping) else {}
    raw_published_at = headers.get("kb_published_at_ms")
    published_at_ms = (
        int(raw_published_at)
        if isinstance(raw_published_at, (int, float)) and not isinstance(raw_published_at, bool)
        else None
    )
    return TaskContext(
        service="mem-knowledge",
        role=role,
        hostname=str(getattr(request, "hostname", None) or socket.gethostname()),
        main_pid=_MAIN_PID,
        pid=os.getpid(),
        task_name=str(getattr(current_task, "name", None) or "unknown"),
        task_id=task_id,
        queue=_safe_header_text(delivery_info.get("routing_key")),
        attempt=int(getattr(request, "retries", 0) or 0),
        knowledge_id=_safe_uuid(knowledge_id),
        document_id=_safe_uuid(document_id),
        trace_id=_safe_header_text(headers.get("kb_trace_id")),
        parent_task_id=_safe_uuid(headers.get("kb_parent_task_id")),
        published_at_ms=published_at_ms,
        started_at_ms=started_at_ms,
        cold_start=child_task_index == 1,
        child_task_index=child_task_index,
    )


__all__ = [
    "BusinessOutcome",
    "CeleryStateSink",
    "DocumentProgressSink",
    "StructuredLogSink",
    "TASK_COUNT_FIELDS",
    "TASK_LOG_FIELDS",
    "TaskContext",
    "TaskEvent",
    "TaskEventSink",
    "TaskRun",
    "current_parent_task_id",
    "error_fingerprint",
    "observe_task",
    "record_task_prerun",
    "reset_observability_after_fork",
    "task_context_from_current_task",
]
