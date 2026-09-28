"""Celery envelopes for the three executable Evidence Graph tasks."""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from celery import states
from celery.exceptions import Ignore, Retry
from redbear_model.errors import (
    PublicCredentialUnavailableError,
    is_provider_rate_limit_error,
)

from ..bootstrap import get_settings
from ..rag.knowledge_graph.config import (
    GraphDocumentDeletionPending,
    GraphPipelineConfigError,
)
from ..rag.knowledge_graph.lock import KnowledgeGraphLockBusy
from ..runtime import get_worker_runtime
from .celery_app import celery_app
from .observability import (
    BusinessOutcome,
    CeleryStateSink,
    StructuredLogSink,
    TaskRun,
    observe_task,
    task_context_from_current_task,
)
from .state import (
    acquire_rebuild_execution,
    has_rebuild_terminal,
    mark_rebuild_terminal,
    refresh_rebuild_job,
    release_rebuild_execution,
    release_rebuild_job,
)

logger = logging.getLogger(__name__)

_FAILURE_RETRIES_HEADER = "kb_graph_failure_retries"
_LOCK_DEFERRALS_HEADER = "kb_graph_lock_deferrals"
_LOCK_RETRY_MIN_SECONDS = 15
_LOCK_RETRY_MAX_SECONDS = 30


def process_evidence_document(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from ..services.evidence_graph import process_evidence_document as process

    return process(*args, **kwargs)


def process_evidence_rebuild(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from ..services.evidence_graph import process_evidence_rebuild as process

    return process(*args, **kwargs)


def process_clear_graph(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from ..services.evidence_graph import process_clear_graph as process

    return process(*args, **kwargs)


def _safe_identifier(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return "invalid"


def _retry_countdown(task: Any) -> int:
    return min(300, 2 ** min(9, _failure_retries(task)))


def _retry_headers(task: Any) -> dict[str, Any]:
    headers = getattr(task.request, "headers", None)
    return dict(headers) if isinstance(headers, Mapping) else {}


def _header_count(headers: Mapping[str, Any], name: str, default: int) -> int:
    value = headers.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GraphPipelineConfigError(f"invalid {name}")
    return value


def _failure_retries(task: Any) -> int:
    return _header_count(
        _retry_headers(task),
        _FAILURE_RETRIES_HEADER,
        int(task.request.retries or 0),
    )


def _retry_available(task: Any, *, max_retries: int | None = None) -> bool:
    retry_limit = task.max_retries if max_retries is None else max_retries
    return retry_limit is None or _failure_retries(task) < retry_limit


def _redacted_exception(exc: Exception) -> RuntimeError:
    return RuntimeError(f"{type(exc).__name__}: message redacted")


def _schedule_retry(
    task: Any,
    run: TaskRun,
    *,
    exc: Exception,
    countdown: int,
    error_code: str,
    lock_busy: bool = False,
) -> None:
    headers = _retry_headers(task)
    failures = _failure_retries(task)
    deferrals = _header_count(headers, _LOCK_DEFERRALS_HEADER, 0)
    headers[_FAILURE_RETRIES_HEADER] = failures + (0 if lock_busy else 1)
    headers[_LOCK_DEFERRALS_HEADER] = deferrals + (1 if lock_busy else 0)
    try:
        # Celery's attempt count includes lock deferrals. The business budget
        # has already been checked separately before permitting this publish.
        retry = task.retry(
            exc=None if lock_busy else _redacted_exception(exc),
            countdown=countdown,
            headers=headers,
            max_retries=int(task.request.retries or 0) + 1,
            throw=False,
        )
    except Exception as publish_error:
        run.finish(
            BusinessOutcome.FAILURE,
            error_code="KB_GRAPH_TASK_RETRY_DISPATCH_FAILED",
            exc=publish_error,
            detail="retry_dispatch_failed",
        )
        raise
    run.finish(
        BusinessOutcome.RETRY,
        error_code=error_code,
        exc=None if lock_busy else exc,
        detail="lock_busy_rescheduled" if lock_busy else f"retry_countdown_{countdown}s",
    )
    raise retry from None


def _run_observed(
    task: Any,
    *,
    run: TaskRun,
    task_name: str,
    knowledge_id: str,
    operation: Callable[[], dict[str, Any]],
    document_id: str | None = None,
    finish_success: bool = True,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    task_id = str(getattr(task.request, "id", None) or "unknown")
    safe_task_id = _safe_identifier(task_id)
    safe_knowledge_id = _safe_identifier(knowledge_id)
    safe_document_id = _safe_identifier(document_id) if document_id is not None else "none"
    retry = _failure_retries(task)
    try:
        with run.stage(task_name):
            result = operation()
    except KnowledgeGraphLockBusy as exc:
        countdown = random.randint(_LOCK_RETRY_MIN_SECONDS, _LOCK_RETRY_MAX_SECONDS)
        logger.info(
            "[EvidenceGraph] task_deferred task=%s task_id=%s kb_id=%s "
            "document_id=%s reason=lock_busy wait_duration_ms=%d delay_seconds=%d "
            "failure_retries=%d lock_deferrals=%d",
            task_name,
            safe_task_id,
            safe_knowledge_id,
            safe_document_id,
            exc.wait_duration_ms,
            countdown,
            retry,
            _header_count(_retry_headers(task), _LOCK_DEFERRALS_HEADER, 0) + 1,
        )
        run.progress(
            stage="lock_wait",
            fraction=None,
            detail="lock_busy_rescheduled",
            wait_duration_ms=exc.wait_duration_ms,
            force=True,
        )
        _schedule_retry(
            task,
            run,
            exc=exc,
            countdown=countdown,
            error_code="KB_GRAPH_LOCK_BUSY",
            lock_busy=True,
        )
    except GraphPipelineConfigError as exc:
        logger.error(
            "[EvidenceGraph] task_failed task=%s task_id=%s kb_id=%s "
            "document_id=%s error_type=%s retry=%d elapsed_ms=%d",
            task_name,
            safe_task_id,
            safe_knowledge_id,
            safe_document_id,
            type(exc).__name__,
            retry,
            int((time.perf_counter() - started_at) * 1000),
        )
        run.finish(
            BusinessOutcome.FAILURE,
            error_code="KB_GRAPH_CONFIG_INVALID",
            exc=exc,
        )
        raise
    except Exception as exc:
        if isinstance(exc, PublicCredentialUnavailableError):
            logger.error(
                "[EvidenceGraph] task_failed task=%s task_id=%s kb_id=%s "
                "document_id=%s status=failure reason=model_unavailable "
                "error_type=%s retry=%d elapsed_ms=%d",
                task_name,
                safe_task_id,
                safe_knowledge_id,
                safe_document_id,
                type(exc).__name__,
                retry,
                int((time.perf_counter() - started_at) * 1000),
            )
            run.finish(
                BusinessOutcome.FAILURE,
                error_code="KB_GRAPH_MODEL_UNAVAILABLE",
                exc=exc,
                detail="model_unavailable",
            )
            raise _redacted_exception(exc) from None
        if is_provider_rate_limit_error(exc):
            logger.error(
                "[EvidenceGraph] task_failed task=%s task_id=%s kb_id=%s "
                "document_id=%s status=failure reason=rate_limited "
                "error_type=%s retry=%d elapsed_ms=%d",
                task_name,
                safe_task_id,
                safe_knowledge_id,
                safe_document_id,
                type(exc).__name__,
                retry,
                int((time.perf_counter() - started_at) * 1000),
            )
            run.finish(
                BusinessOutcome.FAILURE,
                error_code="KB_GRAPH_RATE_LIMITED",
                exc=exc,
                detail="provider_rate_limited",
            )
            raise _redacted_exception(exc) from None
        countdown = _retry_countdown(task)
        retry_options = {}
        if isinstance(exc, GraphDocumentDeletionPending):
            retry_options["max_retries"] = 8
        will_retry = _retry_available(
            task,
            max_retries=retry_options.get("max_retries"),
        )
        if will_retry:
            logger.warning(
                "[EvidenceGraph] task_retry task=%s task_id=%s kb_id=%s "
                "document_id=%s error_type=%s retry=%d countdown=%d elapsed_ms=%d",
                task_name,
                safe_task_id,
                safe_knowledge_id,
                safe_document_id,
                type(exc).__name__,
                retry,
                countdown,
                int((time.perf_counter() - started_at) * 1000),
            )
        else:
            logger.error(
                "[EvidenceGraph] task_failed task=%s task_id=%s kb_id=%s "
                "document_id=%s status=failure reason=retries_exhausted "
                "error_type=%s retry=%d elapsed_ms=%d",
                task_name,
                safe_task_id,
                safe_knowledge_id,
                safe_document_id,
                type(exc).__name__,
                retry,
                int((time.perf_counter() - started_at) * 1000),
            )
        if will_retry:
            _schedule_retry(
                task,
                run,
                exc=exc,
                countdown=countdown,
                error_code="KB_GRAPH_TASK_RETRY",
            )
        run.finish(
            BusinessOutcome.FAILURE,
            error_code="KB_GRAPH_TASK_RETRIES_EXHAUSTED",
            exc=exc,
            detail="retries_exhausted",
        )
        raise _redacted_exception(exc) from None
    logger.info(
        "[EvidenceGraph] task_done task=%s task_id=%s kb_id=%s document_id=%s "
        "status=%s elapsed_ms=%d",
        task_name,
        safe_task_id,
        safe_knowledge_id,
        safe_document_id,
        str(result.get("status") or "completed"),
        int((time.perf_counter() - started_at) * 1000),
    )
    status = str(result.get("status") or "completed")
    if finish_success:
        run.finish(
            BusinessOutcome.SKIPPED if status == "skipped" else BusinessOutcome.SUCCESS,
            detail=str(result.get("reason") or status),
        )
    return result


def _retry_guard(
    task: Any,
    knowledge_id: str,
    exc: Exception,
    run: TaskRun,
) -> None:
    countdown = _retry_countdown(task)
    will_retry = _retry_available(task)
    if will_retry:
        logger.warning(
            "[EvidenceGraph] task_guard_retry task=rebuild_knowledge kb_id=%s "
            "error_type=%s countdown=%d",
            _safe_identifier(knowledge_id),
            type(exc).__name__,
            countdown,
        )
    else:
        logger.error(
            "[EvidenceGraph] task_guard_failed task=rebuild_knowledge kb_id=%s "
            "status=failure reason=retries_exhausted error_type=%s",
            _safe_identifier(knowledge_id),
            type(exc).__name__,
        )
    if will_retry:
        _schedule_retry(
            task,
            run,
            exc=exc,
            countdown=countdown,
            error_code="KB_GRAPH_GUARD_RETRY",
        )
    run.finish(
        BusinessOutcome.FAILURE,
        error_code="KB_GRAPH_GUARD_RETRIES_EXHAUSTED",
        exc=exc,
        detail="retries_exhausted",
    )
    raise _redacted_exception(exc) from None


def _release_execution(redis: Any, knowledge_id: str, owner_token: str) -> None:
    try:
        released = release_rebuild_execution(redis, knowledge_id, owner_token)
        if not released:
            logger.warning(
                "[EvidenceGraph] task_guard_release_skipped task=rebuild_knowledge "
                "kb_id=%s guard=execution",
                _safe_identifier(knowledge_id),
            )
    except Exception as exc:
        logger.error(
            "[EvidenceGraph] task_guard_release_failed task=rebuild_knowledge "
            "kb_id=%s guard=execution error_type=%s",
            _safe_identifier(knowledge_id),
            type(exc).__name__,
        )


def _finish_guard(
    redis: Any,
    *,
    task_id: str,
    knowledge_id: str,
    owner_token: str,
    terminal: str,
) -> None:
    try:
        mark_rebuild_terminal(redis, task_id, terminal)
    finally:
        _release_execution(redis, knowledge_id, owner_token)
    try:
        released = release_rebuild_job(redis, knowledge_id, task_id)
        if not released:
            logger.warning(
                "[EvidenceGraph] task_guard_release_skipped task=rebuild_knowledge "
                "task_id=%s kb_id=%s guard=job",
                _safe_identifier(task_id),
                _safe_identifier(knowledge_id),
            )
    except Exception as exc:
        logger.error(
            "[EvidenceGraph] task_guard_release_failed task=rebuild_knowledge "
            "task_id=%s kb_id=%s guard=job error_type=%s",
            _safe_identifier(task_id),
            _safe_identifier(knowledge_id),
            type(exc).__name__,
        )


def _run_guarded_rebuild(
    task: Any,
    knowledge_id: str,
    run: TaskRun,
) -> dict[str, Any]:
    runtime = get_worker_runtime()
    redis = runtime.redis.sync_client()
    task_id = str(getattr(task.request, "id", None) or "unknown")
    owner_token = f"{task_id}:{uuid.uuid4()}"
    try:
        if has_rebuild_terminal(redis, task_id):
            raise Ignore()
        if not refresh_rebuild_job(redis, knowledge_id, task_id):
            raise Ignore()
        if not acquire_rebuild_execution(redis, knowledge_id, owner_token):
            raise Ignore()
    except Ignore:
        logger.info(
            "[EvidenceGraph] task_coalesced task=rebuild_knowledge task_id=%s kb_id=%s",
            _safe_identifier(task_id),
            _safe_identifier(knowledge_id),
        )
        run.finish(
            BusinessOutcome.COALESCED,
            detail="rebuild_job_coalesced",
        )
        raise
    except Exception as exc:
        _retry_guard(task, knowledge_id, exc, run)

    try:
        task.update_state(
            state=states.STARTED,
            meta={
                "knowledge_id": _safe_identifier(knowledge_id),
                "retry": int(getattr(task.request, "retries", 0) or 0),
            },
        )
    except Exception as exc:
        _release_execution(redis, knowledge_id, owner_token)
        _retry_guard(task, knowledge_id, exc, run)

    try:
        result = _run_observed(
            task,
            run=run,
            task_name="rebuild_knowledge",
            knowledge_id=knowledge_id,
            operation=lambda: process_evidence_rebuild(
                runtime,
                knowledge_id,
                on_lock_wait=_lock_wait_callback(run),
                on_stage=_graph_stage_callback(run),
            ),
            finish_success=False,
        )
    except Retry:
        _release_execution(redis, knowledge_id, owner_token)
        raise
    except Exception:
        _finish_guard(
            redis,
            task_id=task_id,
            knowledge_id=knowledge_id,
            owner_token=owner_token,
            terminal="failure",
        )
        raise
    try:
        _finish_guard(
            redis,
            task_id=task_id,
            knowledge_id=knowledge_id,
            owner_token=owner_token,
            terminal="success",
        )
    except Exception as exc:
        _retry_guard(task, knowledge_id, exc, run)
    status = str(result.get("status") or "completed")
    run.finish(
        BusinessOutcome.SKIPPED if status == "skipped" else BusinessOutcome.SUCCESS,
        detail=str(result.get("reason") or status),
    )
    return result


def _observe_graph_task(
    task: Any,
    *,
    knowledge_id: object,
    document_id: object | None = None,
) -> TaskRun:
    context = task_context_from_current_task(
        role="graphrag_worker",
        knowledge_id=knowledge_id,
        document_id=document_id,
    )
    return observe_task(
        context,
        sinks=[
            StructuredLogSink(),
            CeleryStateSink(task.update_state, task_id=context.task_id),
        ],
        heartbeat_seconds=get_settings().kb_task_heartbeat_seconds,
    )


def _lock_wait_callback(run: TaskRun) -> Callable[[str, int], None]:
    def report(detail: str, wait_duration_ms: int) -> None:
        run.progress(
            stage="lock_wait",
            fraction=None,
            detail=detail,
            wait_duration_ms=wait_duration_ms,
            force=True,
        )

    return report


def _graph_stage_callback(
    run: TaskRun,
) -> Callable[[str, str, int, Mapping[str, int]], None]:
    def report(
        event: str,
        stage: str,
        duration_ms: int,
        counts: Mapping[str, int],
    ) -> None:
        run.progress(
            stage=stage,
            fraction=None,
            detail=f"stage_{event}",
            duration_ms=duration_ms,
            counts=counts,
        )

    return report


@celery_app.task(
    bind=True,
    name="app.core.rag.tasks.sync_evidence_graph_document",
    max_retries=5,
)
def sync_evidence_graph_document(
    self: Any,
    knowledge_id: str,
    document_id: str,
    document_deleted: bool = False,
) -> dict[str, Any]:
    with _observe_graph_task(
        self,
        knowledge_id=knowledge_id,
        document_id=document_id,
    ) as run:
        runtime = get_worker_runtime()
        return _run_observed(
            self,
            run=run,
            task_name="sync_document",
            knowledge_id=str(knowledge_id),
            document_id=str(document_id),
            operation=lambda: process_evidence_document(
                runtime,
                knowledge_id,
                document_id,
                document_deleted=document_deleted,
                on_lock_wait=_lock_wait_callback(run),
                on_stage=_graph_stage_callback(run),
            ),
        )


@celery_app.task(
    bind=True,
    name="app.core.rag.tasks.rebuild_evidence_graph_knowledge",
    max_retries=5,
    acks_late=False,
    reject_on_worker_lost=False,
    track_started=False,
)
def rebuild_evidence_graph_knowledge(
    self: Any,
    knowledge_id: str,
) -> dict[str, Any]:
    with _observe_graph_task(self, knowledge_id=knowledge_id) as run:
        return _run_guarded_rebuild(self, str(knowledge_id), run)


@celery_app.task(
    bind=True,
    name="app.core.rag.tasks.clear_all_knowledge_graph_data",
    max_retries=5,
)
def clear_all_knowledge_graph_data(
    self: Any,
    knowledge_id: str,
    force: bool = False,
) -> dict[str, Any]:
    with _observe_graph_task(self, knowledge_id=knowledge_id) as run:
        runtime = get_worker_runtime()
        return _run_observed(
            self,
            run=run,
            task_name="clear_knowledge",
            knowledge_id=str(knowledge_id),
            operation=lambda: process_clear_graph(
                runtime,
                knowledge_id,
                force=force,
                on_lock_wait=_lock_wait_callback(run),
                on_stage=_graph_stage_callback(run),
            ),
        )


__all__ = [
    "clear_all_knowledge_graph_data",
    "rebuild_evidence_graph_knowledge",
    "sync_evidence_graph_document",
]
