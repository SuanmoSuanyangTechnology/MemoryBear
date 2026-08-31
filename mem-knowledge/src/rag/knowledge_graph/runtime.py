"""Resolve immutable Evidence Graph model and index snapshots."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from ...models.owned import Knowledge
from ...models.references import Workspace
from ..vdb.vector_store import collection_name_for_knowledge
from .config import GraphPipelineConfigError, is_graph_enabled, require_graph_mapping
from .elasticsearch_store import graph_index_name
from .models import GraphIndexRuntime


class GraphRuntimeDisabled(GraphPipelineConfigError):
    """The graph was disabled between the task state and runtime snapshots."""


def snapshot_graph_runtime(runtime: object, knowledge_id: str) -> GraphIndexRuntime:
    from ..models.task_runtime import TaskModelFactory

    try:
        knowledge_uuid = uuid.UUID(str(knowledge_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise GraphPipelineConfigError("invalid knowledge id") from exc

    with runtime.database.sync_session() as session:
        knowledge = session.get(Knowledge, knowledge_uuid)
        if knowledge is None:
            raise GraphPipelineConfigError("knowledge does not exist")
        if not is_graph_enabled(knowledge.parser_config):
            raise GraphRuntimeDisabled("knowledge graph is disabled")
        workspace = (
            session.execute(select(Workspace).where(Workspace.id == knowledge.workspace_id))
            .scalars()
            .first()
        )
        if workspace is None:
            raise GraphPipelineConfigError("workspace does not exist")
        if knowledge.llm_id is None or knowledge.embedding_id is None:
            raise GraphPipelineConfigError("graph runtime requires both LLM and embedding models")
        graph_config = require_graph_mapping(knowledge.parser_config)
        raw_entity_types = graph_config.get("entity_types") or ()
        if not isinstance(raw_entity_types, (list, tuple)):
            raise GraphPipelineConfigError("graphrag.entity_types must be a list")
        workspace_id = str(workspace.id)
        tenant_id = workspace.tenant_id
        llm_id = knowledge.llm_id
        embedding_id = knowledge.embedding_id
        entity_types = tuple(value for item in raw_entity_types if (value := str(item).strip()))
        scene_name = str(graph_config.get("scene_name") or "")

    factory = TaskModelFactory(runtime)
    return GraphIndexRuntime(
        knowledge_id=str(knowledge_uuid),
        workspace_id=workspace_id,
        graph_index_name=graph_index_name(workspace_id),
        chunk_index_name=collection_name_for_knowledge(knowledge_uuid),
        entity_types=entity_types,
        scene_name=scene_name,
        llm=factory.resolve_chat(llm_id, tenant_id),
        embedding=factory.resolve_embedding(embedding_id, tenant_id),
    )


__all__ = ["GraphRuntimeDisabled", "snapshot_graph_runtime"]
