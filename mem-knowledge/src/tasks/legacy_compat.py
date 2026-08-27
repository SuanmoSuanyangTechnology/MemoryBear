"""Compatibility tombstones for removed legacy graph tasks."""

from __future__ import annotations

import logging
import uuid

from .celery_app import celery_app

logger = logging.getLogger(__name__)


def _safe_identifier(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except Exception:
        return "invalid"


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_kb")
def build_graphrag_for_kb(kb_id: object) -> str:
    logger.warning(
        "Legacy task removed; skipped task=build_graphrag_for_kb kb_id=%s",
        _safe_identifier(kb_id),
    )
    return "build knowledge graph skipped: legacy task removed"


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_document")
def build_graphrag_for_document(document_id: object, knowledge_id: object) -> str:
    logger.warning(
        "Legacy task removed; skipped task=build_graphrag_for_document kb_id=%s document_id=%s",
        _safe_identifier(knowledge_id),
        _safe_identifier(document_id),
    )
    safe_document_id = _safe_identifier(document_id)
    return f"build_graphrag_for_document '{safe_document_id}' skipped: legacy task removed"


@celery_app.task(name="app.core.rag.tasks.migrate_evidence_graph_knowledge")
def migrate_evidence_graph_knowledge(knowledge_id: object) -> dict[str, str]:
    logger.warning(
        "Legacy task removed; skipped task=migrate_evidence_graph_knowledge kb_id=%s",
        _safe_identifier(knowledge_id),
    )
    return {
        "status": "skipped",
        "reason": "legacy_task_removed",
        "knowledge_id": _safe_identifier(knowledge_id),
    }
