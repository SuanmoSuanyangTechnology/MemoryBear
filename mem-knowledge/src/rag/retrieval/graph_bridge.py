"""Pipeline-explicit graph retrieval with Legacy empty-result compatibility."""

from __future__ import annotations

import logging
import time
from typing import Any

from ...integrations.model.chat import RedBearChatModel
from ...integrations.model.embedding import RedBearEmbeddings
from ...integrations.model.invoke_backend import ref_from_view
from ...integrations.model.views import is_qwen3_vl_embedding_view
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
            pipeline = KnowledgeGraphRetrievalPipeline(
                graph_store,
                RedBearChatModel.for_invoke_ref(
                    ref_from_view(target.llm, target.llm.tenant_id),
                    pool=runtime.model_runtime,
                    params={"temperature": 0},
                ),
                RedBearEmbeddings.for_invoke_ref(
                    ref_from_view(target.embedding, target.embedding.tenant_id),
                    pool=runtime.model_runtime,
                    multimodal=is_qwen3_vl_embedding_view(target.embedding),
                ),
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
                        llm=target.llm,
                        embedding=target.embedding,
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
