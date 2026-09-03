"""Synchronous graph API behavior and compatible command dispatch."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from redbear_model.runtime import RedBearLLM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..errors import KnowledgeError
from ..models.owned import Knowledge
from ..rag.knowledge_graph.config import GraphPipeline, is_graph_enabled, resolve_graph_pipeline
from ..rag.knowledge_graph.elasticsearch_store import graph_index_name
from ..rag.parser_config import set_graph_pipeline_for_migration

logger = logging.getLogger(__name__)


async def graph_entity_types(runtime: Any, model_config: Any, scenario: str) -> str:
    prompt = (
        "## Role\nYou are a knowledge graph entity type identifier.\n\n"
        "## Task\nIdentify and extract all relevant entity types for constructing a "
        "knowledge graph based on a given scenario.\n\n"
        "## Requirements\n"
        "- Analyze the scenario and determine key entity categories.\n"
        "- Return all applicable entity types as an English comma-delimited list.\n"
        "- Entity types must be lowercase and use underscores for multi-word terms.\n"
        "- Output only the entity types, no explanations or additional text.\n\n"
        f"## Real Data\n\n**Scenario:**\n\n{scenario}\n"
    )
    model = RedBearLLM(model_config, client_pool=runtime.model_runtime.pool)
    response = await model.ainvoke(prompt)
    content = getattr(response, "content", response)
    text = str(content)
    text = re.sub(r"^.*</think>", "", text, flags=re.DOTALL).strip()
    return "" if "**ERROR**" in text else text


async def get_graph(knowledge: Any, store: Any) -> dict[str, Any]:
    index_name = graph_index_name(str(knowledge.workspace_id))
    pipeline = resolve_graph_pipeline(knowledge.parser_config)
    if pipeline is GraphPipeline.EVIDENCE:
        graph = await store.load_projection_graph(index_name, str(knowledge.id))
        return {"graph": graph, "mind_map": {}}
    logger.warning(
        "Legacy graph detail is unavailable; returning an empty result: knowledge=%s",
        knowledge.id,
    )
    return {"graph": {}, "mind_map": {}}


async def commit_evidence_pipeline(
    db: AsyncSession,
    knowledge_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Knowledge:
    result = await db.execute(
        select(Knowledge)
        .where(
            Knowledge.id == knowledge_id,
            Knowledge.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    knowledge = result.scalars().first()
    if knowledge is None:
        raise KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", "Knowledge resource not found")
    if not is_graph_enabled(knowledge.parser_config):
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR",
            "knowledge graph is not enabled",
        )
    knowledge.parser_config = set_graph_pipeline_for_migration(
        knowledge.parser_config,
        GraphPipeline.EVIDENCE,
    )
    flag_modified(knowledge, "parser_config")
    try:
        await db.commit()
        await db.refresh(knowledge)
    except Exception:
        await db.rollback()
        raise
    return knowledge


async def delete_graph(knowledge: Any, dispatcher: Any) -> str:
    return await dispatcher.send(
        "app.core.rag.tasks.clear_all_knowledge_graph_data",
        args=[str(knowledge.id)],
        kwargs={"force": True},
    )


async def rebuild_graph(knowledge: Any, store: Any, dispatcher: Any) -> str:
    del store
    if not is_graph_enabled(knowledge.parser_config):
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "knowledge graph is not enabled")
    return await dispatcher.send(
        "app.core.rag.tasks.rebuild_evidence_graph_knowledge",
        args=[str(knowledge.id)],
    )


__all__ = [
    "commit_evidence_pipeline",
    "delete_graph",
    "get_graph",
    "graph_entity_types",
    "rebuild_graph",
]
