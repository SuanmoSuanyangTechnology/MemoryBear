"""Document worker task envelopes."""

from __future__ import annotations

from ..bootstrap import get_settings
from ..runtime import get_worker_runtime
from .celery_app import celery_app
from .observability import (
    DocumentProgressSink,
    StructuredLogSink,
    observe_task,
    task_context_from_current_task,
)


def _load_document_processor():
    """Load the model-heavy document processor only when the task executes."""

    from ..services.document_processing import process_document as process

    return process


def process_document(*args, **kwargs):
    """Compatibility helper that delegates to the lazy document processor."""

    return _load_document_processor()(*args, **kwargs)


def _load_knowledge_sync_processor():
    """Load external integration clients only when synchronization executes."""

    from ..services.knowledge_sync import process_knowledge_sync as process

    return process


def process_knowledge_sync(*args, **kwargs):
    """Compatibility helper that delegates to the lazy sync processor."""

    return _load_knowledge_sync_processor()(*args, **kwargs)


@celery_app.task(name="app.core.rag.tasks.parse_document")
def parse_document(file_key: str, document_id, file_name: str = ""):
    settings = get_settings()
    runtime = get_worker_runtime()
    context = task_context_from_current_task(
        role="document_worker",
        document_id=document_id,
    )
    sinks = [
        StructuredLogSink(),
        DocumentProgressSink(
            runtime=runtime,
            document_id=str(document_id),
            flush_interval_seconds=settings.kb_progress_flush_interval_seconds,
        ),
    ]
    with observe_task(
        context,
        sinks=sinks,
        heartbeat_seconds=settings.kb_task_heartbeat_seconds,
    ) as run:
        with run.stage("lazy_import"):
            process = _load_document_processor()
        return process(
            runtime,
            file_key,
            document_id,
            file_name,
            run=run,
        )


@celery_app.task(name="app.core.rag.tasks.sync_knowledge_for_kb")
def sync_knowledge_for_kb(kb_id):
    settings = get_settings()
    runtime = get_worker_runtime()
    context = task_context_from_current_task(
        role="document_worker",
        knowledge_id=kb_id,
    )
    with observe_task(
        context,
        sinks=[StructuredLogSink()],
        heartbeat_seconds=settings.kb_task_heartbeat_seconds,
    ) as run:
        with run.stage("lazy_import"):
            process = _load_knowledge_sync_processor()
        return process(runtime, kb_id, run=run)


__all__ = ["parse_document", "sync_knowledge_for_kb"]
