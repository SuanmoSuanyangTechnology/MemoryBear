"""Wiki task envelopes with persisted retry budgets and late ACK handoffs."""

from __future__ import annotations

import time

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError

from ..rag.wiki.config import WikiDeferred, WikiError
from ..runtime import get_worker_runtime
from ..usage_context import bind_usage
from .celery_app import celery_app
from .observability import (
    BusinessOutcome,
    StructuredLogSink,
    observe_task,
    task_context_from_current_task,
)


def _safe_error(exc):
    return exc if isinstance(exc, WikiError) else WikiError("WIKI_EXECUTION_FAILED")


def _record_failure(task, run, runtime, operation, *args, **kwargs):
    from ..rag.wiki.config import WikiSuperseded
    from ..services.wiki.runtime import transaction

    try:
        return transaction(runtime, operation, *args, **kwargs)
    except WikiSuperseded:
        return False
    except (OperationalError, InterfaceError, DatabaseTimeoutError):
        # Never ACK an accepted delivery when even its terminal state cannot be saved.
        # This only retries state availability; model/lock budgets remain persisted and finite.
        run.finish(BusinessOutcome.RETRY, error_code="WIKI_STATE_UNAVAILABLE")
        raise task.retry(
            exc=RuntimeError("WIKI_STATE_UNAVAILABLE"),
            countdown=runtime.settings.kb_wiki_retry_delay_seconds,
            max_retries=None,
        ) from None


@celery_app.task(bind=True, name="mem_knowledge.tasks.sync_wiki_document", max_retries=None)
@bind_usage("document", "document_id")
def sync_wiki_document(self, kb_id, document_id, requested_version, operation="upsert"):
    from ..rag.wiki.config import WikiSuperseded
    from ..repositories.wiki.failures import map_failure
    from ..services.wiki.execution import execute_map

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
            result = execute_map(runtime, delivery)
            run.finish(BusinessOutcome.SUCCESS)
            return result
        except WikiSuperseded:
            run.finish(BusinessOutcome.SKIPPED)
            return {"status": "superseded"}
        except Exception as exc:
            error = _safe_error(exc)
            retry = _record_failure(
                self,
                run,
                runtime,
                map_failure,
                delivery,
                error,
                runtime.settings,
                deferred=isinstance(exc, WikiDeferred),
            )
            if retry:
                run.finish(BusinessOutcome.RETRY)
                raise self.retry(
                    exc=RuntimeError(error.code),
                    countdown=runtime.settings.kb_wiki_retry_delay_seconds,
                ) from None
            run.finish(BusinessOutcome.FAILURE, error_code=error.code)
            return {"status": "failed", "error_code": error.code}


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
            error = _safe_error(exc)
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
                run.finish(BusinessOutcome.RETRY)
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
    from ..services.wiki.backfill import execute_backfill

    runtime = get_worker_runtime()
    context = task_context_from_current_task(role="wiki_worker", knowledge_id=kb_id)
    with observe_task(
        context,
        sinks=[StructuredLogSink()],
        heartbeat_seconds=runtime.settings.kb_task_heartbeat_seconds,
    ) as run:
        try:
            result = execute_backfill(runtime, kb_id, upper_at_ms, str(self.request.id))
            run.finish(BusinessOutcome.SUCCESS)
            return result
        except Exception as exc:
            error = _safe_error(exc)
            if self.request.retries < self.max_retries:
                run.finish(BusinessOutcome.RETRY)
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
