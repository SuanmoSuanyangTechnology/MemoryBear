"""Document worker task envelopes."""

from __future__ import annotations

from ..runtime import get_worker_runtime
from ..services.knowledge_sync import process_knowledge_sync
from .celery_app import celery_app


@celery_app.task(name="app.core.rag.tasks.sync_knowledge_for_kb")
def sync_knowledge_for_kb(kb_id):
    return process_knowledge_sync(get_worker_runtime(), kb_id)


__all__ = ["sync_knowledge_for_kb"]
