from collections.abc import Mapping
from typing import Any

from app.celery_app import celery_app
from app.core.rag.knowledge_graph.config import (
    GraphPipeline,
    is_graph_enabled,
    resolve_graph_pipeline,
)


def dispatch_document_graph_sync(
    knowledge_id: str,
    document_id: str,
    parser_config: Mapping[str, Any] | None,
    *,
    dispatch_legacy: bool = True,
) -> Any | None:
    if not is_graph_enabled(parser_config):
        return None

    pipeline = resolve_graph_pipeline(parser_config)
    if pipeline is GraphPipeline.LEGACY:
        if not dispatch_legacy:
            return None
        return celery_app.send_task(
            "app.core.rag.tasks.build_graphrag_for_document",
            args=[str(document_id), str(knowledge_id)],
        )
    return celery_app.send_task(
        "app.core.rag.tasks.sync_evidence_graph_document",
        args=[str(knowledge_id), str(document_id)],
    )


def dispatch_knowledge_graph_rebuild(
    knowledge_id: str,
    parser_config: Mapping[str, Any] | None,
) -> Any | None:
    if not is_graph_enabled(parser_config):
        return None

    pipeline = resolve_graph_pipeline(parser_config)
    task_name = (
        "app.core.rag.tasks.build_graphrag_for_kb"
        if pipeline is GraphPipeline.LEGACY
        else "app.core.rag.tasks.rebuild_evidence_graph_knowledge"
    )
    return celery_app.send_task(task_name, args=[str(knowledge_id)])
