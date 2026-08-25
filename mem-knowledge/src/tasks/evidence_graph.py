"""Celery envelopes for the three executable Evidence Graph tasks."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from celery import states
from celery.exceptions import Ignore, Retry

from ..rag.knowledge_graph.config import GraphPipelineConfigError
from ..runtime import get_worker_runtime
from ..services.evidence_graph import (
    process_clear_graph,
    process_evidence_document,
    process_evidence_rebuild,
)
from .celery_app import celery_app
from .state import (
    acquire_rebuild_execution,
    has_rebuild_terminal,
    mark_rebuild_terminal,
    refresh_rebuild_job,
    release_rebuild_execution,
    release_rebuild_job,
)

logger = logging.getLogger(__name__)


def _safe_identifier(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return "invalid"


def _retry_countdown(task: Any) -> int:
    return min(300, 2 ** int(task.request.retries or 0))


def _redacted_exception(exc: Exception) -> RuntimeError:
    return RuntimeError(f"{type(exc).__name__}: message redacted")


def _run_observed(
    task: Any,
    *,
    task_name: str,
    knowledge_id: str,
    operation: Callable[[], dict[str, Any]],
    document_id: str | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    task_id = str(getattr(task.request, "id", None) or "unknown")
    safe_task_id = _safe_identifier(task_id)
    safe_knowledge_id = _safe_identifier(knowledge_id)
    safe_document_id = _safe_identifier(document_id) if document_id is not None else "none"
    retry = int(getattr(task.request, "retries", 0) or 0)
    try:
        result = operation()
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
        raise
    except Exception as exc:
        countdown = _retry_countdown(task)
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
        raise task.retry(exc=_redacted_exception(exc), countdown=countdown) from None
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
    return result


def _retry_guard(task: Any, knowledge_id: str, exc: Exception) -> None:
    countdown = _retry_countdown(task)
    logger.warning(
        "[EvidenceGraph] task_guard_retry task=rebuild_knowledge kb_id=%s "
        "error_type=%s countdown=%d",
        _safe_identifier(knowledge_id),
        type(exc).__name__,
        countdown,
    )
    raise task.retry(exc=_redacted_exception(exc), countdown=countdown) from None


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


def _run_guarded_rebuild(task: Any, knowledge_id: str) -> dict[str, Any]:
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
        raise
    except Exception as exc:
        _retry_guard(task, knowledge_id, exc)

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
        _retry_guard(task, knowledge_id, exc)

    try:
        result = _run_observed(
            task,
            task_name="rebuild_knowledge",
            knowledge_id=knowledge_id,
            operation=lambda: process_evidence_rebuild(runtime, knowledge_id),
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
        _retry_guard(task, knowledge_id, exc)
    return result


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
    runtime = get_worker_runtime()
    return _run_observed(
        self,
        task_name="sync_document",
        knowledge_id=str(knowledge_id),
        document_id=str(document_id),
        operation=lambda: process_evidence_document(
            runtime,
            knowledge_id,
            document_id,
            document_deleted=document_deleted,
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
    return _run_guarded_rebuild(self, str(knowledge_id))


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
    runtime = get_worker_runtime()
    return _run_observed(
        self,
        task_name="clear_knowledge",
        knowledge_id=str(knowledge_id),
        operation=lambda: process_clear_graph(runtime, knowledge_id, force=force),
    )


__all__ = [
    "clear_all_knowledge_graph_data",
    "rebuild_evidence_graph_knowledge",
    "sync_evidence_graph_document",
]
