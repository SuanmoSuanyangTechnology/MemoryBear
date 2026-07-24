import logging
from collections.abc import Mapping
from typing import Any

from app.celery_app import celery_app
from app.core.rag.knowledge_graph.config import (
    GraphPipeline,
    is_graph_enabled,
    resolve_graph_pipeline,
)

logger = logging.getLogger(__name__)


def _log_dispatched_task(
    task: Any,
    *,
    scope: str,
    pipeline: GraphPipeline,
    task_name: str,
    knowledge_id: str,
    document_id: str | None = None,
) -> None:
    document_field = (
        f" document_id={document_id}" if document_id is not None else ""
    )
    logger.info(
        "[GraphPipeline] task_dispatched"
        " scope=%s pipeline=%s task=%s kb_id=%s%s task_id=%s",
        scope,
        pipeline.value,
        task_name,
        str(knowledge_id),
        document_field,
        str(getattr(task, "id", None) or "unknown"),
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
        task_name = "app.core.rag.tasks.build_graphrag_for_document"
        task = celery_app.send_task(
            task_name,
            args=[str(document_id), str(knowledge_id)],
        )
    else:
        task_name = "app.core.rag.tasks.sync_evidence_graph_document"
        task = celery_app.send_task(
            task_name,
            args=[str(knowledge_id), str(document_id)],
        )
    _log_dispatched_task(
        task,
        scope="document",
        pipeline=pipeline,
        task_name=task_name,
        knowledge_id=str(knowledge_id),
        document_id=str(document_id),
    )
    return task


async def enqueue_document_graph_sync(
    knowledge_id: str,
    document_id: str,
    parser_config: Mapping[str, Any] | None,
    *,
    dispatch_legacy: bool = True,
) -> str | None:
    if not is_graph_enabled(parser_config):
        return None

    pipeline = resolve_graph_pipeline(parser_config)
    if pipeline is GraphPipeline.LEGACY:
        if not dispatch_legacy:
            return None
        task_name = "app.core.rag.tasks.build_graphrag_for_document"
        params = {
            "document_id": str(document_id),
            "knowledge_id": str(knowledge_id),
        }
    else:
        task_name = "app.core.rag.tasks.sync_evidence_graph_document"
        params = {
            "knowledge_id": str(knowledge_id),
            "document_id": str(document_id),
        }

    from app.celery_task_scheduler import scheduler as celery_scheduler

    msg_id = await celery_scheduler.push_task(
        task_name,
        str(knowledge_id),
        params,
    )
    logger.info(
        "[GraphPipeline] task_persisted"
        " scope=document pipeline=%s task=%s kb_id=%s"
        " document_id=%s msg_id=%s",
        pipeline.value,
        task_name,
        str(knowledge_id),
        str(document_id),
        str(msg_id),
    )
    return str(msg_id)


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
    task = celery_app.send_task(task_name, args=[str(knowledge_id)])
    _log_dispatched_task(
        task,
        scope="knowledge",
        pipeline=pipeline,
        task_name=task_name,
        knowledge_id=str(knowledge_id),
    )
    return task


def dispatch_graph_enabled_transition(
    knowledge_id: str,
    previous_enabled: bool,
    parser_config: Mapping[str, Any] | None,
) -> Any | None:
    current_enabled = is_graph_enabled(parser_config)
    if current_enabled == previous_enabled:
        return None
    if current_enabled:
        return dispatch_knowledge_graph_rebuild(
            str(knowledge_id),
            parser_config,
        )
    pipeline = resolve_graph_pipeline(parser_config)
    task_name = "app.core.rag.tasks.clear_all_knowledge_graph_data"
    task = celery_app.send_task(
        task_name,
        args=[str(knowledge_id)],
    )
    _log_dispatched_task(
        task,
        scope="knowledge",
        pipeline=pipeline,
        task_name=task_name,
        knowledge_id=str(knowledge_id),
    )
    return task
