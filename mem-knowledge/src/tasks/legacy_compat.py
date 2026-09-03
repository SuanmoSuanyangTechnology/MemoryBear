"""Compatibility tombstones for removed legacy graph tasks."""

from __future__ import annotations

import logging
import uuid

from ..bootstrap import get_settings
from .celery_app import celery_app
from .observability import (
    BusinessOutcome,
    StructuredLogSink,
    observe_task,
    task_context_from_current_task,
)

logger = logging.getLogger(__name__)


def _safe_identifier(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except Exception:
        return "invalid"


def _observe_tombstone(
    *,
    knowledge_id: object,
    document_id: object | None = None,
):
    context = task_context_from_current_task(
        role="graphrag_worker",
        knowledge_id=knowledge_id,
        document_id=document_id,
    )
    return observe_task(
        context,
        sinks=[StructuredLogSink()],
        heartbeat_seconds=get_settings().kb_task_heartbeat_seconds,
    )


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_kb")
def build_graphrag_for_kb(kb_id: object) -> str:
    with _observe_tombstone(knowledge_id=kb_id) as run:
        logger.warning(
            "Legacy task removed; skipped task=build_graphrag_for_kb kb_id=%s",
            _safe_identifier(kb_id),
        )
        run.finish(BusinessOutcome.SKIPPED, detail="legacy_task_removed")
        return "build knowledge graph skipped: legacy task removed"


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_document")
def build_graphrag_for_document(document_id: object, knowledge_id: object) -> str:
    with _observe_tombstone(
        knowledge_id=knowledge_id,
        document_id=document_id,
    ) as run:
        logger.warning(
            "Legacy task removed; skipped task=build_graphrag_for_document "
            "kb_id=%s document_id=%s",
            _safe_identifier(knowledge_id),
            _safe_identifier(document_id),
        )
        safe_document_id = _safe_identifier(document_id)
        run.finish(BusinessOutcome.SKIPPED, detail="legacy_task_removed")
        return f"build_graphrag_for_document '{safe_document_id}' skipped: legacy task removed"


@celery_app.task(name="app.core.rag.tasks.migrate_evidence_graph_knowledge")
def migrate_evidence_graph_knowledge(knowledge_id: object) -> dict[str, str]:
    with _observe_tombstone(knowledge_id=knowledge_id) as run:
        logger.warning(
            "Legacy task removed; skipped task=migrate_evidence_graph_knowledge kb_id=%s",
            _safe_identifier(knowledge_id),
        )
        run.finish(BusinessOutcome.SKIPPED, detail="legacy_task_removed")
        return {
            "status": "skipped",
            "reason": "legacy_task_removed",
            "knowledge_id": _safe_identifier(knowledge_id),
        }
