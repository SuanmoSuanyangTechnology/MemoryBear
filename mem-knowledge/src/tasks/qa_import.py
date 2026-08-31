"""QA import Celery task envelope."""

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


def _load_qa_processor():
    """Load the model-heavy QA processor only when the task executes."""

    from ..services.qa_import_processing import process_qa_import as process

    return process


def process_qa_import(*args, **kwargs):
    """Compatibility helper that delegates to the lazy QA processor."""

    return _load_qa_processor()(*args, **kwargs)


@celery_app.task(name="app.core.rag.tasks.import_qa_chunks", queue="qa_import")
def import_qa_chunks(
    kb_id: str,
    document_id: str,
    filename: str,
    contents: bytes | None = None,
    file_key: str | None = None,
    clear_parse_task: bool = False,
):
    settings = get_settings()
    runtime = get_worker_runtime()
    context = task_context_from_current_task(
        role="qa_import_worker",
        knowledge_id=kb_id,
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
            process = _load_qa_processor()
        return process(
            runtime,
            kb_id,
            document_id,
            filename,
            contents=contents,
            file_key=file_key,
            clear_parse_task=clear_parse_task,
            run=run,
        )


__all__ = ["import_qa_chunks"]
