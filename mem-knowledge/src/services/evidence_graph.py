"""Evidence Graph task services with short database sessions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from ..models.owned import Document, Knowledge
from ..rag.knowledge_graph.config import (
    GraphPipeline,
    GraphPipelineConfigError,
    is_graph_enabled,
    resolve_graph_pipeline,
)
from ..rag.knowledge_graph.elasticsearch_store import (
    GraphElasticsearchStore,
    graph_index_name,
)
from ..rag.knowledge_graph.extraction_cache import GraphExtractionCache
from ..rag.knowledge_graph.extractor import LLMEntityRelationExtractor
from ..rag.knowledge_graph.index_pipeline import KnowledgeGraphIndexPipeline
from ..rag.knowledge_graph.lock import create_knowledge_graph_lock
from ..rag.knowledge_graph.models import GraphTaskState
from ..rag.knowledge_graph.runtime import snapshot_graph_runtime


class GraphDocumentDeletionPending(RuntimeError):
    """The graph cleanup must wait until document deletion is committed."""


def _canonical_uuid(value: object, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise GraphPipelineConfigError(f"invalid {field_name}") from exc


def load_graph_task_state(
    runtime: Any,
    knowledge_id: str,
    document_id: str | None = None,
    *,
    include_active_documents: bool = False,
) -> GraphTaskState:
    knowledge_uuid = uuid.UUID(_canonical_uuid(knowledge_id, "knowledge id"))
    document_uuid = (
        uuid.UUID(_canonical_uuid(document_id, "document id")) if document_id is not None else None
    )
    with runtime.database.sync_session() as session:
        knowledge = session.get(Knowledge, knowledge_uuid)
        if knowledge is None:
            raise GraphPipelineConfigError("knowledge does not exist")
        document_active: bool | None = None
        if document_uuid is not None:
            document = (
                session.execute(
                    select(Document).where(
                        Document.id == document_uuid,
                        Document.kb_id == knowledge_uuid,
                    )
                )
                .scalars()
                .first()
            )
            document_active = None if document is None else document.status == 1
        active_document_ids: tuple[str, ...] = ()
        if include_active_documents:
            active_document_ids = tuple(
                str(value)
                for value in session.execute(
                    select(Document.id)
                    .where(
                        Document.kb_id == knowledge_uuid,
                        Document.status == 1,
                        Document.chunk_num > 0,
                    )
                    .order_by(Document.id)
                )
                .scalars()
                .all()
            )
        return GraphTaskState(
            knowledge_id=str(knowledge.id),
            workspace_id=str(knowledge.workspace_id),
            pipeline=resolve_graph_pipeline(knowledge.parser_config),
            graph_enabled=is_graph_enabled(knowledge.parser_config),
            document_active=document_active,
            active_document_ids=active_document_ids,
        )


async def _build_pipeline(runtime: Any, graph_runtime: Any, lock_guard: Any):
    from redbear_model.runtime import RedBearEmbeddings, RedBearLLM

    client = await runtime.elasticsearch.client()
    redis = await runtime.redis.client()
    llm = RedBearLLM(graph_runtime.llm, client_pool=runtime.model_runtime.pool)
    embedding = RedBearEmbeddings(
        graph_runtime.embedding,
        client_pool=runtime.model_runtime.pool,
    )
    extractor = LLMEntityRelationExtractor(
        llm,
        graph_runtime.entity_types,
        graph_runtime.scene_name,
    )
    return KnowledgeGraphIndexPipeline(
        store=GraphElasticsearchStore(client),
        extractor=extractor,
        embedding=embedding,
        lock_guard=lock_guard,
        extraction_cache=GraphExtractionCache(redis),
    )


def execute_evidence_document(
    runtime: Any,
    state: GraphTaskState,
    document_id: str,
    lock_guard: Any,
    *,
    document_active: bool,
) -> None:
    graph_runtime = snapshot_graph_runtime(runtime, state.knowledge_id)

    async def run() -> None:
        pipeline = await _build_pipeline(runtime, graph_runtime, lock_guard)
        await pipeline.sync_document(graph_runtime, document_id, document_active)

    runtime.run_async(run)


def execute_evidence_rebuild(
    runtime: Any,
    state: GraphTaskState,
    lock_guard: Any,
) -> None:
    graph_runtime = snapshot_graph_runtime(runtime, state.knowledge_id)

    async def run() -> None:
        pipeline = await _build_pipeline(runtime, graph_runtime, lock_guard)
        await pipeline.rebuild_knowledge(graph_runtime, state.active_document_ids)

    runtime.run_async(run)


def execute_evidence_clear(
    runtime: Any,
    state: GraphTaskState,
    lock_guard: Any,
) -> None:
    async def run() -> None:
        store = GraphElasticsearchStore(await runtime.elasticsearch.client())
        await store.clear_all_graph_documents(
            graph_index_name(state.workspace_id),
            state.knowledge_id,
            ensure_valid=lock_guard.ensure_valid,
        )

    runtime.run_async(run)


def process_evidence_document(
    runtime: Any,
    knowledge_id: str,
    document_id: str,
    *,
    document_deleted: bool = False,
) -> dict[str, Any]:
    knowledge_id = _canonical_uuid(knowledge_id, "knowledge id")
    document_id = _canonical_uuid(document_id, "document id")
    with create_knowledge_graph_lock(runtime, knowledge_id) as lock_guard:
        lock_guard.ensure_valid()
        state = load_graph_task_state(runtime, knowledge_id, document_id)
        if state.pipeline is not GraphPipeline.EVIDENCE:
            return {"status": "skipped", "reason": "pipeline_changed"}
        if not state.graph_enabled:
            return {"status": "skipped", "reason": "graph_disabled"}
        if document_deleted and state.document_active is not None:
            raise GraphDocumentDeletionPending("document deletion has not been committed")
        execute_evidence_document(
            runtime,
            state,
            document_id,
            lock_guard,
            document_active=(False if document_deleted else bool(state.document_active)),
        )
        lock_guard.ensure_valid()
        return {
            "status": "completed",
            "knowledge_id": knowledge_id,
            "document_id": document_id,
        }


def process_evidence_rebuild(runtime: Any, knowledge_id: str) -> dict[str, Any]:
    knowledge_id = _canonical_uuid(knowledge_id, "knowledge id")
    with create_knowledge_graph_lock(runtime, knowledge_id) as lock_guard:
        lock_guard.ensure_valid()
        state = load_graph_task_state(
            runtime,
            knowledge_id,
            include_active_documents=True,
        )
        if state.pipeline is not GraphPipeline.EVIDENCE:
            return {"status": "skipped", "reason": "pipeline_changed"}
        if not state.graph_enabled:
            return {"status": "skipped", "reason": "graph_disabled"}
        execute_evidence_rebuild(runtime, state, lock_guard)
        lock_guard.ensure_valid()
        return {"status": "completed", "knowledge_id": knowledge_id}


def process_clear_graph(
    runtime: Any,
    knowledge_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    knowledge_id = _canonical_uuid(knowledge_id, "knowledge id")
    with create_knowledge_graph_lock(runtime, knowledge_id) as lock_guard:
        lock_guard.ensure_valid()
        state = load_graph_task_state(runtime, knowledge_id)
        if state.graph_enabled and not force:
            return {"status": "skipped", "reason": "graph_reenabled"}
        execute_evidence_clear(runtime, state, lock_guard)
        lock_guard.ensure_valid()
        return {"status": "cleared", "knowledge_id": knowledge_id}


__all__ = [
    "GraphDocumentDeletionPending",
    "execute_evidence_clear",
    "execute_evidence_document",
    "execute_evidence_rebuild",
    "load_graph_task_state",
    "process_clear_graph",
    "process_evidence_document",
    "process_evidence_rebuild",
]
