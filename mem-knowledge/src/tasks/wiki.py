"""Wiki task envelopes with persisted retry budgets and late ACK handoffs."""

from __future__ import annotations

import logging
import time

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError

from ..rag.wiki.config import WikiDeferred, WikiError
from ..rag.wiki.diagnostics import diagnostic_json, exception_details
from ..runtime import get_worker_runtime
from ..usage_context import bind_usage
from .celery_app import celery_app
from .observability import (
    BusinessOutcome,
    StructuredLogSink,
    observe_task,
    task_context_from_current_task,
)

_LOGGER = logging.getLogger(__name__)


def _safe_error(exc):
    return exc if isinstance(exc, WikiError) else WikiError("WIKI_EXECUTION_FAILED")


def _report_error(run, exc, *, code=None):
    """保留完整失败原因，并把可展示的诊断信息传递给文档进度日志。"""
    error = WikiError(code) if code else _safe_error(exc)
    context = run.context
    diagnostic = {
        **exception_details(exc),
        "event": "wiki_task_error",
        "message": "Wiki 任务延后执行" if isinstance(exc, WikiDeferred) else "Wiki 任务执行失败",
        "task_id": context.task_id,
        "task_name": context.task_name,
        "knowledge_id": context.knowledge_id,
        "document_id": context.document_id,
        "page_id": context.page_id,
        "attempt": context.attempt,
        "trace_id": context.trace_id,
        "error_code": error.code,
    }
    error.diagnostics = diagnostic
    level = logging.INFO if isinstance(exc, WikiDeferred) else logging.ERROR
    _LOGGER.log(level, "%s", diagnostic_json(diagnostic))
    return error


def _record_failure(task, run, runtime, operation, *args, **kwargs):
    from ..rag.wiki.config import WikiSuperseded
    from ..services.wiki.runtime import transaction

    try:
        return transaction(runtime, operation, *args, **kwargs)
    except WikiSuperseded:
        return False
    except (OperationalError, InterfaceError, DatabaseTimeoutError) as exc:
        # Never ACK an accepted delivery when even its terminal state cannot be saved.
        # This only retries state availability; model/lock budgets remain persisted and finite.
        _report_error(run, exc, code="WIKI_STATE_UNAVAILABLE")
        run.finish(BusinessOutcome.RETRY, error_code="WIKI_STATE_UNAVAILABLE")
        raise task.retry(
            exc=RuntimeError("WIKI_STATE_UNAVAILABLE"),
            countdown=runtime.settings.kb_wiki_retry_delay_seconds,
            max_retries=None,
        ) from None


def _run_map(task, run, runtime, delivery, ensure_owned):
    """真正执行和失败结算共用 Map 租约，避免释放后旧回调覆盖新执行者。"""
    from ..rag.wiki.config import WikiSuperseded
    from ..repositories.wiki.failures import map_failure
    from ..services.wiki.execution import execute_map

    try:
        result = execute_map(runtime, delivery, ensure_owned=ensure_owned)
        run.finish(BusinessOutcome.SUCCESS)
        return result
    except WikiSuperseded:
        run.finish(BusinessOutcome.SKIPPED)
        return {"status": "superseded"}
    except Exception as exc:
        ensure_owned()
        error = _report_error(run, exc)
        retry = _record_failure(
            task, run, runtime, map_failure, delivery, error, runtime.settings,
            deferred=isinstance(exc, WikiDeferred),
        )
        if retry:
            run.finish(BusinessOutcome.RETRY, error_code=error.code)
            raise task.retry(
                exc=RuntimeError(error.code),
                countdown=runtime.settings.kb_wiki_retry_delay_seconds,
            ) from None
        run.finish(BusinessOutcome.FAILURE, error_code=error.code)
        return {"status": "failed", "error_code": error.code}


@celery_app.task(bind=True, name="mem_knowledge.tasks.sync_wiki_document", max_retries=None)
@bind_usage("document", "document_id")
def sync_wiki_document(self, kb_id, document_id, requested_version, operation="upsert"):
    from ..services.wiki.lease import LEASE_TTL_SECONDS, map_lease

    runtime = get_worker_runtime()
    delivery = {
        "kb_id": kb_id,
        "document_id": document_id,
        "requested_version": requested_version,
        "operation": operation,
        "task_id": str(self.request.id),
        "redelivered": bool((self.request.delivery_info or {}).get("redelivered")),
    }
    context = task_context_from_current_task(
        role="wiki_worker", knowledge_id=kb_id, document_id=document_id
    )
    with observe_task(
        context,
        sinks=[StructuredLogSink()],
        heartbeat_seconds=runtime.settings.kb_task_heartbeat_seconds,
    ) as run:
        try:
            with map_lease(runtime.redis.sync_client(), kb_id, document_id,
                           requested_version) as ensure_owned:
                return _run_map(self, run, runtime, delivery, ensure_owned)
        except WikiDeferred as exc:
            # 未取得或无法确认租约时只恢复消息交接，不能改写另一执行者的文档状态。
            error = _report_error(run, exc)
            run.finish(BusinessOutcome.RETRY, error_code=error.code)
            raise self.retry(
                exc=RuntimeError(error.code),
                countdown=max(LEASE_TTL_SECONDS, runtime.settings.kb_wiki_retry_delay_seconds),
                max_retries=None,
            ) from None


@celery_app.task(bind=True, name="mem_knowledge.tasks.reduce_wiki_page", max_retries=None)
@bind_usage("kb", "kb_id")
def reduce_wiki_page(self, kb_id, page_id, participants=None):
    from ..repositories.wiki.failures import page_failure
    from ..services.wiki.execution import execute_reduce

    runtime = get_worker_runtime()
    context = task_context_from_current_task(
        role="wiki_worker", knowledge_id=kb_id, page_id=page_id
    )
    with observe_task(
        context,
        sinks=[StructuredLogSink()],
        heartbeat_seconds=runtime.settings.kb_task_heartbeat_seconds,
    ) as run:
        try:
            result = execute_reduce(
                runtime,
                kb_id,
                page_id,
                redelivered=bool((self.request.delivery_info or {}).get("redelivered")),
            )
            run.finish(BusinessOutcome.SUCCESS)
            return result
        except Exception as exc:
            error = _report_error(run, exc)
            retry = _record_failure(
                self,
                run,
                runtime,
                page_failure,
                kb_id,
                page_id,
                error,
                runtime.settings,
                deferred=isinstance(exc, WikiDeferred),
                snapshot=getattr(exc, "wiki_snapshot", None),
            )
            if retry:
                run.finish(BusinessOutcome.RETRY, error_code=error.code)
                raise self.retry(
                    exc=RuntimeError(error.code),
                    countdown=runtime.settings.kb_wiki_retry_delay_seconds,
                ) from None
            run.finish(BusinessOutcome.FAILURE, error_code=error.code)
            return {"status": "failed", "error_code": error.code}


@celery_app.task(
    bind=True, name="mem_knowledge.tasks.enqueue_unprocessed_wiki_documents", max_retries=3
)
@bind_usage("kb", "kb_id")
def enqueue_unprocessed_wiki_documents(self, kb_id, upper_at_ms):
    from ..rag.wiki.config import BACKFILL_REGISTERED_HEADER
    from ..services.wiki.backfill import execute_backfill

    runtime = get_worker_runtime()
    context = task_context_from_current_task(role="wiki_worker", knowledge_id=kb_id)
    with observe_task(
        context,
        sinks=[StructuredLogSink()],
        heartbeat_seconds=runtime.settings.kb_task_heartbeat_seconds,
    ) as run:
        try:
            registered_only = (self.request.headers or {}).get(BACKFILL_REGISTERED_HEADER) is True
            result = execute_backfill(
                runtime, kb_id, upper_at_ms, str(self.request.id),
                registered_only=registered_only,
            )
            run.finish(BusinessOutcome.SUCCESS)
            return result
        except Exception as exc:
            error = _report_error(run, exc)
            if self.request.retries < self.max_retries:
                run.finish(BusinessOutcome.RETRY, error_code=error.code)
                raise self.retry(
                    exc=RuntimeError(error.code),
                    countdown=runtime.settings.kb_wiki_retry_delay_seconds,
                ) from None
            run.finish(BusinessOutcome.FAILURE, error_code=error.code)
            return {
                "status": "failed",
                "error_code": error.code,
                "failed_at_ms": int(time.time() * 1000),
            }
