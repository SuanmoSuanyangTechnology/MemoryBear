"""Pipeline-explicit graph retrieval with Legacy empty-result compatibility."""

from __future__ import annotations

import logging
import time
from typing import Any

from redbear_model.runtime import RedBearEmbeddings, RedBearLLM

from ...runtime import ProcessRuntime
from ..knowledge_graph.config import GraphPipeline
from ..knowledge_graph.elasticsearch_store import GraphElasticsearchStore
from ..knowledge_graph.models import GraphIndexRuntime, GraphRetrievalRequest
from ..knowledge_graph.query_plan_cache import GraphQueryPlanCache
from ..knowledge_graph.retrieval_pipeline import KnowledgeGraphRetrievalPipeline
from ..models.chunk import DocumentChunk
from .async_elasticsearch import AsyncElasticSearchRetrieval
from .models import GraphRetrievalSnapshot

logger = logging.getLogger(__name__)


class GraphRetrievalBridge:
    @staticmethod
    async def retrieve(
        runtime: ProcessRuntime,
        client: Any,
        snapshot: GraphRetrievalSnapshot,
        *,
        top_k: int,
        allowed_document_ids: tuple[str, ...] | None,
        file_names: tuple[str, ...],
    ) -> tuple[list[DocumentChunk], list[dict[str, Any]], list[dict[str, Any]]]:
        if snapshot.pipeline is GraphPipeline.LEGACY:
            logger.warning("Legacy graph retrieval is unavailable; returning an empty result")
            return [], [], []

        chunks: list[DocumentChunk] = []
        entities: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        graph_store = GraphElasticsearchStore(client)
        chunk_store = AsyncElasticSearchRetrieval(client)
        query_plan_cache = GraphQueryPlanCache(runtime.redis.client)
        model_pool = runtime.model_runtime.pool

        async def resolve_parent_chunks(
            chunks: list[DocumentChunk],
            index: str,
        ) -> list[DocumentChunk]:
            has_parent = any(
                (chunk.metadata or {}).get("chunk_type") == "child"
                and (chunk.metadata or {}).get("parent_id")
                for chunk in chunks
            )
            if not has_parent:
                return chunks
            started_at = time.perf_counter()
            try:
                return await chunk_store.resolve_parent_chunks(chunks, index)
            finally:
                if snapshot.timings is not None:
                    elapsed_ms = max(
                        0,
                        int((time.perf_counter() - started_at) * 1000),
                    )
                    snapshot.timings.parent_resolution_ms += elapsed_ms

        for target in snapshot.targets:
            if target.llm.resolved is None or target.embedding.resolved is None:
                raise ValueError("graph retrieval model snapshot is unavailable")
            llm_params = dict(target.llm.resolved.provider_params)
            llm_params["temperature"] = 0
            llm_config = target.llm.resolved.model_copy(
                update={"provider_params": llm_params},
                deep=True,
            )
            pipeline = KnowledgeGraphRetrievalPipeline(
                graph_store,
                RedBearLLM(llm_config, client_pool=model_pool),
                RedBearEmbeddings(target.embedding.resolved, client_pool=model_pool),
                resolve_parent_chunks,
                query_plan_cache,
                timeout_ms=runtime.settings.knowledge_graph_retrieval_timeout_ms,
            )
            result = await pipeline.retrieve_with_graph_data(
                GraphRetrievalRequest(
                    query=snapshot.query,
                    runtime=GraphIndexRuntime(
                        knowledge_id=str(target.knowledge_id),
                        workspace_id=str(target.workspace_id),
                        graph_index_name=target.graph_index_name,
                        chunk_index_name=target.chunk_index_name,
                        entity_types=(),
                        scene_name="",
                        llm=llm_config,
                        embedding=target.embedding.resolved,
                    ),
                    allowed_document_ids=allowed_document_ids,
                    file_names=file_names,
                    max_candidates=top_k,
                )
            )
            chunks.extend(result.chunks)
            entities.extend(result.entities)
            relationships.extend(result.relationships)
        return chunks, entities, relationships


__all__ = ["GraphRetrievalBridge"]
