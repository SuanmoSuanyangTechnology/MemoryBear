"""Document worker task envelopes."""

from __future__ import annotations

from ..runtime import get_worker_runtime
from ..services.knowledge_sync import process_knowledge_sync
from .celery_app import celery_app


def process_document(*args, **kwargs):
    """Load the model-heavy document processor only when the task executes."""

    from ..services.document_processing import process_document as process

    return process(*args, **kwargs)


@celery_app.task(name="app.core.rag.tasks.parse_document")
def parse_document(file_key: str, document_id, file_name: str = ""):
    return process_document(
        get_worker_runtime(),
        file_key,
        document_id,
        file_name,
    )


@celery_app.task(name="app.core.rag.tasks.sync_knowledge_for_kb")
def sync_knowledge_for_kb(kb_id):
    return process_knowledge_sync(get_worker_runtime(), kb_id)


__all__ = ["parse_document", "sync_knowledge_for_kb"]
